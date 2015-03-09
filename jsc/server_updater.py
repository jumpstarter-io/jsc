import subprocess
import os
import os.path


def main():
    fnull = open(os.devnull, 'w')
    client_version = os.environ["JSC_CLIENT_VERSION"]
    if os.path.isfile("/tmp/server"):
        server_version = subprocess.check_output("/tmp/server --version", shell=True, stderr=fnull).strip()
        if server_version == client_version:
            return
    # /tmp/server does not exist or is not the correct version

    subprocess.check_output("curl -f -o /tmp/server http://jsc.jumpstarter.io/server-{version}".format(version=client_version), shell=True, stderr=fnull)
    subprocess.check_output("chmod +x /tmp/server".format(version=client_version), shell=True, stderr=fnull)


if __name__ == "__main__":
    main()
