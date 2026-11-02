"""One bounded kubectl -> GNU grep pipeline, with stdin only and no shell."""
import shlex
from .candidate_kubectl import CandidateCommandError


def split_filter(command):
    if type(command) is not str or not 1 <= len(command) <= 600 or any(c in command for c in '\n\r\0'):
        raise CandidateCommandError()
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars='|')
        lexer.whitespace_split = True
        lexer.commenters = ''
        words = list(lexer)
    except ValueError:
        raise CandidateCommandError() from None
    if '|' not in words:
        return command, None
    if words.count('|') != 1:
        raise CandidateCommandError()
    index = words.index('|')
    if index < 3 or words[index+1:index+2] != ['grep']:
        raise CandidateCommandError()
    args = words[index+2:]
    flags = []
    while args and args[0].startswith('-') and args[0] != '--':
        flag = args.pop(0)
        if not 2 <= len(flag) <= 10 or not set(flag[1:]) <= set('nivFExwc') or 'F' in flag and 'E' in flag:
            raise CandidateCommandError()
        flags.append(flag)
    if args[:1] == ['--']:
        args.pop(0)
    if len(args) != 1 or not args[0] or len(args[0]) > 128 or any(ord(c) < 32 for c in args[0]):
        raise CandidateCommandError()
    if any('F' in flag for flag in flags) and any('E' in flag for flag in flags):
        raise CandidateCommandError()
    return shlex.join(words[:index]), ['/usr/bin/grep','--color=never',*flags,'--',args[0]]
