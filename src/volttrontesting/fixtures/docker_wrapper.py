import os
import subprocess

try:
    import docker
    from docker.errors import APIError, ImageNotFound
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False

import time
import contextlib

# Only allow this function if docker is available from the pip library.
if HAS_DOCKER:

    @contextlib.contextmanager
    def create_container(image_name: str, ports: dict = None, env: dict = None, command: (list, str) = None,
                         startup_time_seconds: int = 30, hostname: str = 'test_docker_env') -> \
            (docker.models.containers.Container, None):
        """ Creates a container instance in a context that will clean up after itself.

        This is a wrapper around the docker api documented at https://docker-py.readthedocs.io/en/stable/containers.html.
        Some things that this will do automatically for the caller is make the container be ephemeral by removing it
        once the context manager is cleaned up.

        This function is being used for long running processes.  Short processes may not work correctly because
        the execution time might be too short for the returning of data from the container.

        Usage:

            with create_container("mysql", {"3306/tcp": 3306}):
                # connect to localhost:3306 with mysql using connector

        :param hostname: Optional hostname for the container. If tests are run within a docker container,
         this code will automatically detect the test container's network and attache the mysql container to the
         same network.
        :param image_name: The image name (from dockerhub) that is to be instantiated
        :param ports:
            a dictionary following the convention {'portincontainer/protocol': portonhost}

            ::
                # example port exposing mysql's known port.
                {'3306/tcp': 3306}
        :param env: environment variables to set inside the container.
        :param command: string or list of commands to run during the startup of the container.
        :param startup_time_seconds: Allow this many seconds for the startup of the container before raising a
            runtime exception (Download of image and instantiation could take a while)

        :return:
            A container object (https://docker-py.readthedocs.io/en/stable/containers.html.) or None
        """

        # Create docker client (Uses localhost as agent connection.
        client = docker.from_env(version="auto")
        network_name = None
        if os.path.exists("/.dockerenv"):
            network_name = list(client.api.inspect_container(os.environ["HOSTNAME"])['NetworkSettings']['Networks'])[0]
        try:
            full_docker_image = image_name
            if ":" not in full_docker_image:
                # So all tags aren't pulled. According to docs https://docker-py.readthedocs.io/en/stable/images.html.
                full_docker_image = full_docker_image + ":latest"

            # only pull image if image has not yet been pulled so we don't exceed pull rate limits for free Dockerhub accounts: https://www.docker.com/increase-rate-limits
            res = subprocess.run(["docker", "image", "inspect", f"{full_docker_image}"], stdout=subprocess.DEVNULL)
            if res.returncode != 0:
                client.images.pull(full_docker_image)

            kwargs = {'ports': ports, 'environment': env, 'auto_remove': True, 'detach': True, 'hostname': hostname,
                      'volumes': {
                          '/var/run/docker.sock': {'bind': '/var/run/docker.sock', 'mode': 'rw'}
                      },
                      'working_dir': os.getcwd()
                      }

            if network_name:
                kwargs['network'] = network_name
            container = client.containers.run(image_name, **kwargs)
        except (ImageNotFound, APIError, RuntimeError) as e:
            raise RuntimeError(e)

        if container is None:
            raise RuntimeError(f"Unable to run image {image_name}")

        # wait for a certain amount of time to let container complete its build
        try:
            if _is_not_valid_container(container, startup_time_seconds):
                yield None
            else:
                yield container
        finally:
            container.kill()

    def _is_not_valid_container(container, startup_time_seconds):
        error_time = time.time() + startup_time_seconds
        invalid = False

        while container.status != 'running':
            if time.time() > error_time:
                invalid = True
                break
            time.sleep(0.1)
            container.reload()

        return invalid
