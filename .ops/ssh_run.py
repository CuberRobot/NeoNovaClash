"""本地运维小工具：通过 SSH 在远端执行命令 / 上传文件（凭据只从环境变量或本机密钥读取，不进版本库）。

用法：
    python .ops/ssh_run.py 'systemctl status neonovaclash --no-pager'
    python .ops/ssh_run.py --put 本地文件 远端路径

认证顺序（2026-09-25 起线上已关闭密码认证，必须走密钥）：
    1. `NC_SSH_KEY` 指定的私钥；
    2. 本机默认私钥 `~/.ssh/id_ed25519`、`~/.ssh/id_rsa`（存在就用）；
    3. 最后才回退到 `NC_SSH_PASS` 密码（给还没配密钥的机器用）。

环境变量：
    NC_SSH_HOST（默认 189.24.77.139）/ NC_SSH_PORT / NC_SSH_USER / NC_SSH_KEY / NC_SSH_PASS
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("NC_SSH_HOST", "189.24.77.139")
PORT = int(os.environ.get("NC_SSH_PORT", "22"))
USER = os.environ.get("NC_SSH_USER", "root")
PASSWORD = os.environ.get("NC_SSH_PASS", "")
DEFAULT_KEYS = ("~/.ssh/id_ed25519", "~/.ssh/id_rsa")


def _key_candidates() -> list[Path]:
    """按优先级列出可用的私钥路径：环境变量指定 > 本机默认密钥。"""

    candidates: list[Path] = []
    if os.environ.get("NC_SSH_KEY"):
        candidates.append(Path(os.environ["NC_SSH_KEY"]).expanduser())
    candidates.extend(Path(path).expanduser() for path in DEFAULT_KEYS)
    return [path for path in candidates if path.exists()]


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    errors: list[str] = []
    for key_path in _key_candidates():
        try:
            client.connect(
                HOST,
                port=PORT,
                username=USER,
                key_filename=str(key_path),
                timeout=25,
                banner_timeout=40,
                auth_timeout=40,
                look_for_keys=False,
                allow_agent=False,
            )
            return client
        except paramiko.SSHException as exc:  # 换下一把钥匙
            errors.append(f"{key_path}: {exc}")
    if PASSWORD:
        try:
            client.connect(
                HOST,
                port=PORT,
                username=USER,
                password=PASSWORD,
                timeout=25,
                banner_timeout=40,
                auth_timeout=40,
                look_for_keys=False,
                allow_agent=False,
            )
            return client
        except paramiko.SSHException as exc:
            errors.append(f"password: {exc}")
    hint = "\n".join(errors) if errors else "没有找到任何私钥，也没有设置 NC_SSH_PASS"
    raise SystemExit(
        f"SSH 连接失败（{USER}@{HOST}:{PORT}）：\n{hint}\n"
        "提示：线上已关闭密码认证，请用 `ssh-copy-id` 装好公钥或设置 NC_SSH_KEY。"
    )


def run(command: str, timeout: int = 900) -> tuple[int, str, str]:
    client = connect()
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        return stdout.channel.recv_exit_status(), out, err
    finally:
        client.close()


def put(local: str, remote: str) -> None:
    client = connect()
    try:
        sftp = client.open_sftp()
        sftp.put(local, remote)
        sftp.close()
    finally:
        client.close()


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--put":
        put(sys.argv[2], sys.argv[3])
        print(f"uploaded {sys.argv[2]} -> {sys.argv[3]}")
    else:
        cmd = sys.argv[1] if len(sys.argv) > 1 else "echo hello"
        code, out, err = run(cmd)
        print(f"$ {cmd}\n[exit {code}]")
        sys.stdout.write(out)
        if err.strip():
            sys.stdout.write("\n--- stderr ---\n" + err)
