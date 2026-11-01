"""Fixed-origin PTY stream. No credentials in URLs, cookies, env or child args.

Fresh central authorization is required at entry and every heartbeat. Only local
editor input streams between heartbeats; Kubernetes mutations require a separate
freshly authorized edit-save operation after the editor has exited.
"""
import asyncio
import json
import time

from starlette.concurrency import run_in_threadpool
from fastapi import WebSocket, WebSocketDisconnect
from .identity_poc import _json_object
from .lab_editor import EditRejected
from .lab_controller import LabDenied, LabBusy, LabRateLimited, LabUnavailable


def install_editor_socket(app, controller, host, origin):
    @app.websocket('/api/labs/editor')
    async def terminal(socket: WebSocket):
        if (socket.headers.getlist('host') != [host] or socket.headers.getlist('origin') != [origin]
                or socket.headers.getlist('cookie') or socket.url.query or not controller.editors):
            await socket.close(code=1008)
            return
        editor = None
        receiver = None
        completed = False
        await socket.accept()
        try:
            first = await asyncio.wait_for(socket.receive_text(),5)
            if len(first.encode()) > 8192:
                raise EditRejected()
            data = _json_object(first.encode())
            if (set(data) != {'type','id','editor','token','columns','rows'} or data['type'] != 'auth'
                    or type(data['token']) is not str or not 1 <= len(data['token']) <= 4096):
                raise EditRejected()
            lease, identity = data['id'], data['editor']
            editor = await run_in_threadpool(controller.operation, data['token'],'edit-check',identity=lease,editor=identity)
            await run_in_threadpool(controller.editors.attach,editor,data['columns'],data['rows'])
            authorized = time.monotonic()
            started, input_bytes, frames, output_bytes = authorized, 0, 0, 0
            receiver = asyncio.create_task(socket.receive_text())
            while True:
                now = time.monotonic()
                if now >= editor.deadline or now-authorized > 12:
                    raise EditRejected('unavailable')
                for _ in range(4):
                    output = controller.editors.read(editor)
                    if not output:
                        break
                    output_bytes += len(output.encode())
                    if output_bytes > 8*1024**2:
                        raise EditRejected('unavailable')
                    await socket.send_json({'type':'output','data':output})
                code = editor.process.poll()
                if code is not None:
                    await socket.send_json({'type':'exit','code':0 if code == 0 else 1})
                    completed = code == 0
                    break
                done, _ = await asyncio.wait([receiver],timeout=0.05)
                if not done:
                    continue
                raw = receiver.result()
                receiver = None
                if len(raw.encode()) > 8192:
                    raise EditRejected()
                frame = _json_object(raw.encode())
                frames += 1
                if now-started >= 60:
                    started, input_bytes, frames = now, 0, 1
                if frames > 1800:
                    raise EditRejected('unavailable')
                if set(frame) == {'type','token'} and frame['type'] == 'auth':
                    if type(frame['token']) is not str or not 1 <= len(frame['token']) <= 4096:
                        raise EditRejected()
                    checked = await run_in_threadpool(controller.operation,frame['token'],'edit-check',identity=lease,editor=identity)
                    if checked is not editor:
                        raise EditRejected('unavailable')
                    authorized = time.monotonic()
                elif set(frame) == {'type','data'} and frame['type'] == 'input' and type(frame['data']) is str:
                    input_bytes += len(frame['data'].encode())
                    if input_bytes > 131072:
                        raise EditRejected('unavailable')
                    controller.editors.input(editor,frame['data'])
                elif set(frame) == {'type','columns','rows'} and frame['type'] == 'resize':
                    controller.editors.resize(editor,frame['columns'],frame['rows'])
                else:
                    raise EditRejected()
                receiver = asyncio.create_task(socket.receive_text())
        except (WebSocketDisconnect, OSError, ValueError, KeyError, TypeError, TimeoutError,
                LabDenied, LabBusy, LabRateLimited, LabUnavailable):
            try:
                await socket.send_json({'type':'error','message':'La edición se ha cerrado. Comprueba tu sesión e inténtalo de nuevo.'})
            except Exception:
                pass
        finally:
            if receiver:
                receiver.cancel()
                try:
                    await receiver
                except (asyncio.CancelledError, WebSocketDisconnect):
                    pass
            if editor and not completed and controller.editors.current is editor:
                await run_in_threadpool(controller.editors.close)
            try:
                await socket.close(code=1000 if completed else 1008)
            except RuntimeError:
                pass
