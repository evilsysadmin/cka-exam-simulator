"""Bounded kubectl grammar for the three curated staging pilot exercises.

Returns arguments only: no shell, subprocess, credentials or authentication.
The controller must first verify fresh Identity, lease ownership and active TTL,
then execute only with the lease's candidate-scoped Kubernetes credentials.
Kubernetes RBAC/admission and runtime isolation remain mandatory boundaries.
"""
from __future__ import annotations

import json
import re
import shlex


class CandidateCommandError(ValueError):
    def __init__(self):
        super().__init__("Unsupported candidate command")


READ = {"pod": "pods", "pods": "pods", "po": "pods",
        "deployment": "deployments", "deployments": "deployments", "deploy": "deployments",
        "replicaset": "replicasets", "replicasets": "replicasets", "rs": "replicasets",
        "service": "services", "services": "services", "svc": "services",
        "endpointslice": "endpointslices", "endpointslices": "endpointslices",
        "event": "events", "events": "events", "ev": "events"}
NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
NAMESPACE = re.compile(r"cka-lab-[a-f0-9]{32}")


def _deny():
    raise CandidateCommandError()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _deny()
        value[key] = item
    return value


def _resource(words):
    if not words:
        _deny()
    resource, separator, name = words[0].partition("/")
    resources = resource.split(",")
    if any(item not in READ for item in resources) or len(set(resources)) != len(resources):
        _deny()
    result = [",".join(READ[item] for item in resources)]
    remaining = words[1:]
    if separator:
        if len(resources) != 1 or not NAME.fullmatch(name):
            _deny()
        result.append(name)
    elif remaining and not remaining[0].startswith("-"):
        if not NAME.fullmatch(remaining[0]) or len(resources) != 1:
            _deny()
        result.append(remaining[0])
        remaining = remaining[1:]
    return result, remaining


def _target(words, kind, name):
    aliases = {"deployment": {"deployment", "deployments", "deploy"},
               "service": {"service", "services", "svc"}}[kind]
    if words and "/" in words[0]:
        resource, item = words[0].split("/", 1)
        if resource not in aliases or item != name:
            _deny()
        return words[1:]
    if len(words) < 2 or words[0] not in aliases or words[1] != name:
        _deny()
    return words[2:]


def _replicas(value):
    if value not in {"0", "1", "2", "3", "4"}:
        _deny()
    return value


def _cpu(value):
    if not re.fullmatch(r"cpu=(?:[1-9][0-9]{0,4}m|[1-9][0-9]?)", value):
        _deny()
    raw = value[4:]
    amount = int(raw[:-1]) if raw.endswith("m") else int(raw) * 1000
    if amount > 16000:
        _deny()
    return value


def plan_candidate_command(command, *, namespace, deployment, service):
    if (not all(type(item) is str for item in (command, namespace, deployment, service))
            or not NAMESPACE.fullmatch(namespace) or not NAME.fullmatch(deployment)
            or service != deployment + "-svc" or not NAME.fullmatch(service)
            or not 1 <= len(command) <= 600
            or any(char in command for char in ("\n", "\r", "\0", ";", "|", "&", "`", "$", ">", "<"))):
        _deny()
    try:
        words = shlex.split(command)
    except ValueError:
        _deny()
    if words in (['k'], ['kubectl']):
        return ['--help', '--namespace', namespace]
    if len(words) < 3 or words[0] not in {"kubectl", "k"}:
        _deny()
    selected = False
    rest = []
    index = 1
    while index < len(words):
        word = words[index]
        if word in {"-n", "--namespace"}:
            index += 1
            if selected or index >= len(words) or words[index] != namespace:
                _deny()
            selected = True
        elif word.startswith(("--namespace=", "-n=")):
            if selected or word.split("=", 1)[1] != namespace:
                _deny()
            selected = True
        else:
            rest.append(word)
        index += 1
    if len(rest) < 2:
        _deny()
    verb, args = rest[0], rest[1:]
    planned = [verb]
    if verb in {"get", "describe"}:
        resources, args = _resource(args)
        planned += resources
        seen = set()
        while args:
            option = args.pop(0)
            if "=" in option:
                flag, value = option.split("=", 1)
            elif option in {"-o", "--output", "-l", "--selector", "--sort-by"} and args:
                flag, value = option, args.pop(0)
            else:
                _deny()
            flag = {"-o": "--output", "-l": "--selector"}.get(flag, flag)
            if verb != "get" or flag in seen:
                _deny()
            seen.add(flag)
            if flag == "--output" and value in {"wide", "json", "yaml", "name"}:
                planned.append(flag + "=" + value)
            elif flag == "--selector" and re.fullmatch(r"app=[a-z0-9][a-z0-9-]{0,62}", value):
                planned.append(flag + "=" + value)
            elif flag == "--sort-by" and value in {".metadata.name", ".metadata.creationTimestamp", ".lastTimestamp"}:
                planned.append(flag + "=" + value)
            else:
                _deny()
    elif verb == "scale":
        args = _target(args, "deployment", deployment)
        if len(args) == 1 and args[0].startswith("--replicas="):
            replicas = args[0].split("=", 1)[1]
        elif len(args) == 2 and args[0] == "--replicas":
            replicas = args[1]
        else:
            _deny()
        planned += ["deployment/" + deployment, "--replicas=" + _replicas(replicas)]
    elif verb == "set":
        if not args or args[0] != "resources":
            _deny()
        args = _target(args[1:], "deployment", deployment)
        planned += ["resources", "deployment/" + deployment, "--containers=web"]
        seen = set()
        for option in args:
            if option == "--containers=web" and "--containers" not in seen:
                seen.add("--containers")
                continue
            flag, _, value = option.partition("=")
            if flag not in {"--requests", "--limits"} or flag in seen:
                _deny()
            seen.add(flag)
            planned.append(flag + "=" + _cpu(value))
        if not seen.intersection({"--requests", "--limits"}):
            _deny()
    elif verb == "patch":
        kind = "service" if args[0].split("/", 1)[0] in {"svc", "service", "services"} else "deployment"
        name = service if kind == "service" else deployment
        args = _target(args, kind, name)
        payload = None
        merge = False
        while args:
            option = args.pop(0)
            if option == "--type=merge" and not merge:
                merge = True
            elif option in {"-p", "--patch"} and args and payload is None:
                payload = args.pop(0)
            else:
                _deny()
        if not merge or payload is None:
            _deny()
        try:
            body = json.loads(payload, object_pairs_hook=_unique)
        except (ValueError, RecursionError):
            _deny()
        if type(body) is not dict or set(body) != {"spec"} or type(body["spec"]) is not dict:
            _deny()
        spec = body["spec"]
        if kind == "deployment":
            if set(spec) != {"replicas"} or type(spec["replicas"]) is not int:
                _deny()
            _replicas(str(spec["replicas"]))
        elif (set(spec) != {"selector"} or type(spec["selector"]) is not dict
              or set(spec["selector"]) != {"app"} or type(spec["selector"]["app"]) is not str
              or not NAME.fullmatch(spec["selector"]["app"])):
            _deny()
        planned += [kind + "/" + name, "--type=merge", "--patch", json.dumps(body, separators=(",", ":"))]
    elif verb == "rollout":
        if args[0] != "status" or _target(args[1:], "deployment", deployment):
            _deny()
        planned += ["status", "deployment/" + deployment, "--timeout=5s"]
    elif verb == "explain":
        if len(args) != 1 or not re.fullmatch(r"(?:deployment|service|pod)(?:[.][A-Za-z][A-Za-z0-9]*){0,10}", args[0]):
            _deny()
        planned += args
    else:
        _deny()
    return planned + ["--namespace", namespace]
