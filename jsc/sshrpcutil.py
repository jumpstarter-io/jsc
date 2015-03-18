DEFAULT_SSH_HOST = "ssh.jumpstarter.io"
DEFAULT_SSH_PORT = 22


class SshRpcCallError(BaseException):
    pass


class SshRpcError(BaseException):
    pass


class SshRpcKeyEncrypted(SshRpcError):
    pass


class SshRpcKeyNoAuthMethod(SshRpcError):
    pass


class SshRpcKeyAuthFailed(SshRpcError):
    pass
