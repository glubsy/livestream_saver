from os import setsid, sep
from typing import Optional
from subprocess import Popen, PIPE, DEVNULL
from datetime import datetime

from livestream_saver.download import YoutubeLiveStream


class HookCommand():
    def __init__(self, cmd, logged) -> None:
        self.cmd : Optional[list] = cmd
        self.logged = logged
        # self.enabled = True

    def spawn_subprocess(self, stream: YoutubeLiveStream):
        if not self.cmd:
            return
        for item in self.cmd:
            if item == r"%VIDEO_URL%":
                self.cmd[self.cmd.index(item)] = stream.url
        try:
            if self.logged:
                program_name = self.cmd[0].split(sep)[-1]
                suffix = "_" + datetime.now().strftime(r"%Y%m%d_%H-%M-%S") + ".log"
                logname = stream.output_dir / (program_name + suffix)
                with open(logname, "wb") as outfile:
                    p = Popen(
                        self.cmd,
                        preexec_fn=setsid,
                        stdin=PIPE,
                        stdout=outfile,
                        stderr=PIPE
                    )
            else:
                p = Popen(
                    self.cmd,
                    preexec_fn=setsid,
                    stdin=DEVNULL,
                    stdout=DEVNULL,
                    stderr=DEVNULL
                )
            stream.logger.info(f"Spawned: {self.cmd} with PID={p.pid}")
        except Exception as e:
            stream.logger.warning(f"Error spawning {self.cmd[0]}: {e}")
            pass
