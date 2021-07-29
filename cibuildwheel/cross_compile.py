import os
import sys
import subprocess
from typing import Dict, Optional
from .docker_container import DockerContainer

# Docker container which will be used to resove dependencies
# while crosss compiling the wheels
native_docker_images = {
    "aarch64": "quay.io/pypa/manylinux2014_aarch64:2021-07-14-67a6e11"
}

# Cross triple for different architectures
cross_triple = {
    "aarch64": "/aarch64-unknown-linux-gnueabi/"
}

def platform_tag_to_arch(platform_tag):
    return platform_tag.replace("manylinux_", '')

# Setup environment to prepare the toolchain
class TargetArchEnvUtil:
    def __init__(self,
            env,
            target_arch=None
    ):
        self.tmp = '/tmp'
        self.host = '/host'
        self.deps = '/install_deps'
        self.host_machine_tmp_in_container = self.host + self.tmp
        self.host_machine_deps_in_container = self.host_machine_tmp_in_container + self.deps
        self.host_machine_deps_usr_in_container = self.host_machine_tmp_in_container + self.deps + "/usr"
        self.host_machine_deps_usr_out_container = self.tmp + self.deps + "/usr"
        self.toolchain_deps = env['CROSS_ROOT'] + cross_triple[target_arch]

def setup_qemu():
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--privileged",
            "hypriot/qemu-register"
        ],
        check=True,
    )

# Install the dependencies into the toolchain
def xc_execute_cmd(
        docker: DockerContainer,
        cmd_str: str,
        before_build: bool,
        target_arch: str,
        env: Optional[Dict[str, str]] = None
):
    invalid_cmd = False
    pip_install_env_create = True
    tmpdirpath=""
    target_arch_env=TargetArchEnvUtil(env, target_arch)

    cmds=[cmd.strip().replace('\t', ' ') for cmd in cmd_str.split("&&")]

    # Copy install_deps.sh script from container's tmp to host machine tmp and use it
    if not os.path.isfile(target_arch_env.tmp+'/install_deps.sh'):
        docker.call(
            [
                'cp',
                target_arch_env.tmp+'/install_deps.sh',
                target_arch_env.host_machine_tmp_in_container
            ]
        )

    for cmd in cmds:
        if cmd.startswith('yum '):

            # Install the dependencies into the emulated docker container and
            # Copy back the installed files into host machine
            print("\nRunning cmd: '" + cmd + "' in target's native container '" + native_docker_images[target_arch] + "' and copy the artifacts into the toolchain\n");
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--volume=/:/host",  # ignored on CircleCI
                    native_docker_images[target_arch],
                    "bash",
                    "-c",
                    target_arch_env.host_machine_tmp_in_container+'/install_deps.sh "' + cmd + '"'
                ],
                check=True,
            )

            # The instaleld dependencies are in /tmp/install_deps on host machine.
            # Copy them into the toolchain
            dir_list = os.listdir(target_arch_env.host_machine_deps_usr_out_container)
            for dir in dir_list:
                docker.call(
                    [
                        'cp',
                        '-rf',
                        target_arch_env.host_machine_deps_usr_in_container + "/" + dir,
                        target_arch_env.toolchain_deps
                    ]
                )
        elif cmd.startswith('pip ') or cmd.startswith('python ') or cmd.startswith('python3 '):
            if pip_install_env_create is True and before_build is True:
                tmpdirpath = docker.call(
                              [
                                  'mktemp',
                                  '-d'
                              ],
                              capture_output=True
                          ).strip()
                env['PATH'] = f'{tmpdirpath}:{env["PATH"]}'

                build_pip = docker.call(
                              [
                                  'which',
                                  'build-pip'
                              ],
                              env=env,
                              capture_output=True
                          ).strip()
                build_pybin = build_pip[:build_pip.rindex('/')]

                docker.call(
                    [
                        'ln',
                        '-s',
                        build_pip,
                        tmpdirpath+'/pip'
                    ],
                    env=env
                )
                docker.call(
                    [
                        'ln',
                        '-s',
                        build_pybin + '/build-pip3',
                        tmpdirpath+'/pip3'
                    ],
                    env=env
                )
                docker.call(
                    [
                        'ln',
                        '-s',
                        build_pybin + '/build-python',
                        tmpdirpath+'/python'
                    ],
                    env=env
                )
                docker.call(
                    [
                        'ln',
                        '-s',
                        build_pybin + '/build-python3',
                        tmpdirpath+'/python3'
                    ],
                    env=env
                )

                pip_install_env_create = False
                #shutil.rmtree(tmpdirpath)
            docker.call(["sh", "-c", cmd], env=env)
        else:
            print("During cross compilation, in wheel build phase, only pip/python/yum related commands are allowed")
            invalid_cmd = True
            break

    docker.call(
        [
            'rm',
            '-rf',
            tmpdirpath
        ]
    )
    if invalid_cmd is True:
        sys.exit(1)
