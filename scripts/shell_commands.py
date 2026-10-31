from scripts.constants import project_env

PRINT_COMMAND = """
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    NC='\033[0m'

    function print_status () {
        echo "${GREEN}$1${NC}"
    }

    function print_error () {
        echo "${RED}$1${NC}" >&2
    }
"""

GET_ADDR = """
for ADDR in $(ip addr show "eth0" | awk '/inet / {print $2}')
do
    if [[ $ADDR == *16 ]]
    then
        RESULT_ADDR=${ADDR::-3}
    fi
done
"""

def get_login_registry_script() -> str:
    return f"""
        {PRINT_COMMAND}
        print_status "Login to registry {project_env.registry_hostname}"
        echo {project_env.registry_password} | docker login {project_env.registry_hostname} --username {project_env.registry_username} --password-stdin
    """

JOIN_SWARM = """
if [ "$(docker info --format '{{.Swarm.LocalNodeState}}')" = "active" ]; then
    print_status "Swarm already initialized"
else
    print_status "Initializing swarm"
    docker swarm init --advertise-addr $RESULT_ADDR
fi
"""

def get_init_swarm_script() -> str:
    return f"""
        {PRINT_COMMAND}
        {GET_ADDR}
        {get_login_registry_script()}
        {JOIN_SWARM}
    """

CREATE_PRODNET = """
if [ -z "$(docker network ls -q -f name=^prodnet$)" ]; then
    print_status "Creating prodnet network"
    docker network create --driver overlay --attachable prodnet
fi
"""

# nginx refuses to start without the default_server certificate, and the real one
# can only be issued by certbot once nginx is serving the acme challenge
CREATE_DUMMY_CERT = """
mkdir -p /app/certbot/certificates/live/dummy /app/certbot/challenge
if [ ! -f /app/certbot/certificates/live/dummy/fullchain.pem ]; then
    print_status "Generating self-signed dummy certificate"
    command -v openssl > /dev/null || apt install -y openssl
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 -subj "/CN=dummy" \\
        -keyout /app/certbot/certificates/live/dummy/privkey.pem \\
        -out /app/certbot/certificates/live/dummy/fullchain.pem
fi
"""

def get_init_balancer_script() -> str:
    return f"""
        {PRINT_COMMAND}
        {GET_ADDR}
        {JOIN_SWARM}
        {CREATE_PRODNET}
        {CREATE_DUMMY_CERT}
    """

RELOAD_NGINX = f"""
{PRINT_COMMAND}
for i in $(seq 1 30); do
    NGINX_CONTAINER=$(docker ps -q -f name=balancer_nginx | head -n 1)
    [ -n "$NGINX_CONTAINER" ] && break
    sleep 2
done
if [ -z "$NGINX_CONTAINER" ]; then
    print_error "nginx container is not running. Check: docker service ps balancer_nginx --no-trunc"
    exit 1
fi
docker exec $NGINX_CONTAINER nginx -s reload
"""

def get_gen_fake_certs_script() -> str:
    return f"""
        rm -rf /app/certbot/certificates/live/dummy
        cp -r /app/certbot/certificates/live/{project_env.project_domain} /app/certbot/certificates/live/dummy
    """

SETUP_DOCKER = """
if [ -x "$(command -v docker)" ]; then
   echo "Docker already installed"
else
    # Add Docker's official GPG key:
    apt update
    apt upgrade -y
    apt install ca-certificates curl -y
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    # Add the repository to Apt sources:
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt update
    apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin -y
fi
"""