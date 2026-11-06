"""Small loopback-only Marionette client for opt-in signed-XPI verification."""
import json
import socket
import time


class Marionette:
    def __init__(self, port, process, timeout=60):
        deadline = time.monotonic() + timeout
        while True:
            if process.poll() is not None:
                raise RuntimeError("Test Firefox exited before Marionette started")
            try:
                self.socket = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)
        self.socket.settimeout(600)
        self.sequence = 0
        self.greeting = self.receive()

    def exact(self, length):
        result = bytearray()
        while len(result) < length:
            chunk = self.socket.recv(length - len(result))
            if not chunk:
                raise ConnectionError("Marionette connection closed")
            result.extend(chunk)
        return bytes(result)

    def receive(self):
        length = bytearray()
        while True:
            value = self.exact(1)
            if value == b":":
                break
            length.extend(value)
        return json.loads(self.exact(int(length)))

    def command(self, name, parameters=None):
        self.sequence += 1
        body = json.dumps([0, self.sequence, name, parameters or {}], ensure_ascii=False).encode("utf8")
        self.socket.sendall(str(len(body)).encode("ascii") + b":" + body)
        reply = self.receive()
        if reply[0] != 1 or reply[1] != self.sequence:
            raise RuntimeError(f"Unexpected Marionette response: {reply}")
        if reply[2]:
            raise RuntimeError(f"{name}: {reply[2]}")
        return reply[3]

    def script(self, source, arguments=None, asynchronous=False):
        result = self.command("WebDriver:ExecuteAsyncScript" if asynchronous else "WebDriver:ExecuteScript",
                              {"script": source, "args": arguments or [], "newSandbox": False, "sandbox": None})
        return result.get("value") if isinstance(result, dict) and "value" in result else result

    def message(self, payload):
        result = self.script("""
            const done=arguments[arguments.length-1];
            Promise.resolve(browser.runtime.sendMessage(arguments[0])).then(
              value=>done({ok:true,value}),error=>done({ok:false,error:String(error)}));
        """, [payload], asynchronous=True)
        if not result.get("ok"):
            raise RuntimeError(result.get("error"))
        return result.get("value")

    def close(self):
        self.socket.close()
