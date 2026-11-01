NGINX_BASE_REDIRECT_TEMPLATE = """
server {
    listen 80;
    server_name {PROJECT_DOMAIN} media.{PROJECT_DOMAIN} {EXTRA_DOMAINS};

    location / {
        return 301 https://$host$request_uri;
    }
}
"""

NGINX_BASE_REDIRECT_NO_MEDIA_TEMPLATE = """
server {
    listen 80;
    server_name {PROJECT_DOMAIN} {EXTRA_DOMAINS};

    location / {
        return 301 https://$host$request_uri;
    }
}
"""

HTTP_UPGRADE_TEMPLATE = """
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
"""

NGINX_DEV_MEDIA_TEMPLATE = """
server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name media.{PROJECT_DOMAIN};

    ssl_certificate /app/ssl/media_{PROJECT_NAME}.crt;
    ssl_certificate_key /app/ssl/media_{PROJECT_NAME}.key;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript;

    resolver $NGINX_LOCAL_RESOLVERS valid=30s;

    location ~ ^/({PROJECT_NAME}-media)/ {
        set $minio{PROJECT_NAME} {PROJECT_NAME}-minio;
        proxy_pass http://$minio{PROJECT_NAME}:9000;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto 'https';
    }


    location ~ ^/ws {
        set $minio{PROJECT_NAME} {PROJECT_NAME}-minio;
        proxy_pass http://$minio{PROJECT_NAME}:9001;
        proxy_http_version 1.1;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $http_host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
    
    location ~ ^/ {
        set $minio{PROJECT_NAME} {PROJECT_NAME}-minio;
        proxy_pass http://$minio{PROJECT_NAME}:9001;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto 'https';
    }

}
"""

CENTRIFUGO_DEV_TEMPLATE = """
server {
    listen 443 ssl;
    
    server_name centrifugo.{PROJECT_DOMAIN};

    ssl_certificate /app/ssl/centrifugo_{PROJECT_NAME}.crt;
    ssl_certificate_key /app/ssl/centrifugo_{PROJECT_NAME}.key;

    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Scheme $scheme;
    proxy_set_header Host $http_host;

    resolver $NGINX_LOCAL_RESOLVERS valid=30s;

    location /connection {
        set $centrifugo{PROJECT_NAME} {PROJECT_NAME}-centrifugo;
        proxy_pass http://$centrifugo{PROJECT_NAME}:8000;
        proxy_http_version 1.1;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $http_host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }

    location / {
        set $centrifugo{PROJECT_NAME} {PROJECT_NAME}-centrifugo;
        proxy_pass http://$centrifugo{PROJECT_NAME}:8000;
    }
}
"""

CENTRIFUGO_PROD_TEMPLATE = """
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    
    server_name centrifugo.{PROJECT_DOMAIN};

    ssl_certificate /etc/letsencrypt/live/centrifugo.{PROJECT_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/centrifugo.{PROJECT_DOMAIN}/privkey.pem;

    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Scheme $scheme;
    proxy_set_header Host $http_host;

    resolver 127.0.0.11 valid=30s;

    location /connection {
        set $centrifugo{PROJECT_NAME} {PROJECT_NAME}-centrifugo;
        proxy_pass http://$centrifugo{PROJECT_NAME}:8000;
        proxy_http_version 1.1;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $http_host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }

    location / {
        set $centrifugo{PROJECT_NAME} {PROJECT_NAME}-centrifugo;
        proxy_pass http://$centrifugo{PROJECT_NAME}:8000;
    }
}
"""


# Application server: composed from location blocks (see nginx/configuration.py).
# {SSL_CERTIFICATE}/{SSL_CERTIFICATE_KEY} and {RESOLVER} differ between dev and prod.
APP_SERVER_TEMPLATE = """
server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name {DOMAIN};

    ssl_certificate {SSL_CERTIFICATE};
    ssl_certificate_key {SSL_CERTIFICATE_KEY};

    client_max_body_size 50000k;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript application/json;

    # Dynamic resolver so nginx starts without the upstream containers.
    resolver {RESOLVER} valid=30s;
{LOCATIONS}
}
"""

PROD_HTTP_TEMPLATE = """
server {
    listen 80;
    server_name {DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}
"""

# {MATCH} is "/" or "~ ^/(api|admin)(/|$)".
DJANGO_LOCATION_TEMPLATE = """
    location {MATCH} {
        set $django{PROJECT_NAME} {PROJECT_NAME}-django;
        proxy_pass http://$django{PROJECT_NAME}:8000;
        proxy_buffer_size   128k;
        proxy_buffers   4 256k;
        proxy_busy_buffers_size   256k;
        proxy_connect_timeout       120;
        proxy_send_timeout          120;
        proxy_read_timeout          120;
        send_timeout                120;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
    }
"""

FASTAPI_LOCATION_TEMPLATE = """
    location {MATCH} {
        set $fastapi{PROJECT_NAME} {PROJECT_NAME}-fastapi;
        proxy_pass http://$fastapi{PROJECT_NAME}:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        # Server-sent events (streaming responses) must reach the client as they are produced.
        proxy_buffering off;
        proxy_cache off;
        proxy_connect_timeout       300;
        proxy_send_timeout          300;
        proxy_read_timeout          300;
        send_timeout                300;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
    }
"""

NEXTJS_LOCATION_TEMPLATE = """
    location {MATCH} {
        set $nextjs{PROJECT_NAME} {PROJECT_NAME}-nextjs;
        proxy_pass http://$nextjs{PROJECT_NAME}:3000;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto 'https';
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
"""
