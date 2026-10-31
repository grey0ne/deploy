NGINX_BASE_REDIRECT_TEMPLATE = """
server {
    listen 80;
    server_name {PROJECT_DOMAIN} media.{PROJECT_DOMAIN} {EXTRA_DOMAINS};

    location / {
        return 301 https://$host$request_uri;
    }
}
"""

NGINX_BASE_REDIRECT_FASTAPI_TEMPLATE = """
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

NGINX_DEV_APP_TEMPLATE = """
server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name {PROJECT_DOMAIN};

    ssl_certificate /app/ssl/{PROJECT_NAME}.crt;
    ssl_certificate_key /app/ssl/{PROJECT_NAME}.key;

    client_max_body_size 50000k;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript;

    resolver $NGINX_LOCAL_RESOLVERS valid=30s;

    location ~ ^/(admin|api|idp|login|complete|static|s)/ {
        set $django{PROJECT_NAME} {PROJECT_NAME}-django;
        proxy_buffer_size   128k;
        proxy_buffers   4 256k;
        proxy_busy_buffers_size   256k;
        proxy_connect_timeout       120;
        proxy_send_timeout          120;
        proxy_read_timeout          120;
        send_timeout                120;
        proxy_pass http://$django{PROJECT_NAME}:8000;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
    }

    location / {
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
}
"""

NGINX_EXTRA_DEV_DOMAINS_TEMPLATE = """
server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name {DOMAIN};

    ssl_certificate /app/ssl/{PROJECT_NAME}_{DOMAIN}.crt;
    ssl_certificate_key /app/ssl/{PROJECT_NAME}_{DOMAIN}.key;

    client_max_body_size 50000k;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript;

    resolver $NGINX_LOCAL_RESOLVERS valid=30s;

    location ~ / {
        set $django{PROJECT_NAME} {PROJECT_NAME}-django;
        proxy_buffer_size   128k;
        proxy_buffers   4 256k;
        proxy_busy_buffers_size   256k;
        proxy_connect_timeout       120;
        proxy_send_timeout          120;
        proxy_read_timeout          120;
        send_timeout                120;
        proxy_pass http://$django{PROJECT_NAME}:8000;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
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

APP_PROD_TEMPLATE = """
server {
    listen 80;
    server_name {PROJECT_DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name {PROJECT_DOMAIN};

    ssl_certificate /etc/letsencrypt/live/{PROJECT_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{PROJECT_DOMAIN}/privkey.pem;

    client_max_body_size 50000k;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript;

    # We need dynamic resolver in order to load nginx without upstreams.
    # IP is valid for docker compose resolver
    resolver 127.0.0.11 valid=30s;

    location ~ ^/(admin|api|idp|login|complete|static|s)/ {
        set $django{PROJECT_NAME} {PROJECT_NAME}-django;
        proxy_pass http://$django{PROJECT_NAME}:8000;
        proxy_buffer_size   128k;
        proxy_buffers   4 256k;
        proxy_connect_timeout       120;
        proxy_send_timeout          120;
        proxy_read_timeout          120;
        send_timeout                120;
        proxy_busy_buffers_size   256k;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
    }


    location / {
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
}
"""


EXTRA_DOMAIN_PROD_TEMPLATE = """
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

server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name {DOMAIN};

    ssl_certificate /etc/letsencrypt/live/{DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{DOMAIN}/privkey.pem;

    client_max_body_size 50000k;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript;

    # We need dynamic resolver in order to load nginx without upstreams.
    # IP is valid for docker compose resolver
    resolver 127.0.0.11 valid=30s;

    location / {
        set $django{PROJECT_NAME} {PROJECT_NAME}-django;
        proxy_pass http://$django{PROJECT_NAME}:8000;
        proxy_buffer_size   128k;
        proxy_buffers   4 256k;
        proxy_connect_timeout       120;
        proxy_send_timeout          120;
        proxy_read_timeout          120;
        send_timeout                120;
        proxy_busy_buffers_size   256k;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
    }
}
"""

FASTAPI_DEV_APP_TEMPLATE = """
server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name {PROJECT_DOMAIN};

    ssl_certificate /app/ssl/{PROJECT_NAME}.crt;
    ssl_certificate_key /app/ssl/{PROJECT_NAME}.key;

    client_max_body_size 50000k;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript application/json;

    resolver $NGINX_LOCAL_RESOLVERS valid=30s;

    location /ws {
        set $fastapi{PROJECT_NAME} {PROJECT_NAME}-fastapi;
        proxy_pass http://$fastapi{PROJECT_NAME}:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
        proxy_connect_timeout       300;
        proxy_send_timeout          300;
        proxy_read_timeout          300;
        send_timeout                300;
    }

    location / {
        set $fastapi{PROJECT_NAME} {PROJECT_NAME}-fastapi;
        proxy_pass http://$fastapi{PROJECT_NAME}:8080;
        proxy_buffer_size   128k;
        proxy_buffers   4 256k;
        proxy_busy_buffers_size   256k;
        proxy_connect_timeout       300;
        proxy_send_timeout          300;
        proxy_read_timeout          300;
        send_timeout                300;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
    }
}
"""

FASTAPI_PROD_APP_TEMPLATE = """
server {
    listen 80;
    server_name {PROJECT_DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name {PROJECT_DOMAIN};

    ssl_certificate /etc/letsencrypt/live/{PROJECT_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{PROJECT_DOMAIN}/privkey.pem;

    client_max_body_size 50000k;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript application/json;

    resolver 127.0.0.11 valid=30s;

    location /ws {
        set $fastapi{PROJECT_NAME} {PROJECT_NAME}-fastapi;
        proxy_pass http://$fastapi{PROJECT_NAME}:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
        proxy_connect_timeout       300;
        proxy_send_timeout          300;
        proxy_read_timeout          300;
        send_timeout                300;
    }

    location / {
        set $fastapi{PROJECT_NAME} {PROJECT_NAME}-fastapi;
        proxy_pass http://$fastapi{PROJECT_NAME}:8080;
        proxy_buffer_size   128k;
        proxy_buffers   4 256k;
        proxy_busy_buffers_size   256k;
        proxy_connect_timeout       300;
        proxy_send_timeout          300;
        proxy_read_timeout          300;
        send_timeout                300;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
    }
}
"""

NGINX_EXTRA_DEV_DOMAINS_FASTAPI_TEMPLATE = """
server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name {DOMAIN};

    ssl_certificate /app/ssl/{PROJECT_NAME}_{DOMAIN}.crt;
    ssl_certificate_key /app/ssl/{PROJECT_NAME}_{DOMAIN}.key;

    client_max_body_size 50000k;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript application/json;

    resolver $NGINX_LOCAL_RESOLVERS valid=30s;

    location /ws {
        set $fastapi{PROJECT_NAME} {PROJECT_NAME}-fastapi;
        proxy_pass http://$fastapi{PROJECT_NAME}:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
        proxy_connect_timeout       300;
        proxy_send_timeout          300;
        proxy_read_timeout          300;
        send_timeout                300;
    }

    location / {
        set $fastapi{PROJECT_NAME} {PROJECT_NAME}-fastapi;
        proxy_pass http://$fastapi{PROJECT_NAME}:8080;
        proxy_buffer_size   128k;
        proxy_buffers   4 256k;
        proxy_busy_buffers_size   256k;
        proxy_connect_timeout       300;
        proxy_send_timeout          300;
        proxy_read_timeout          300;
        send_timeout                300;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
    }
}
"""

EXTRA_DOMAIN_PROD_FASTAPI_TEMPLATE = """
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

server {
    listen 443 ssl;
    access_log /dev/stdout;

    server_name {DOMAIN};

    ssl_certificate /etc/letsencrypt/live/{DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{DOMAIN}/privkey.pem;

    client_max_body_size 50000k;

    gzip on;
    gzip_min_length 1000;
    gzip_proxied any;
    gzip_types text/css application/x-javascript application/json;

    resolver 127.0.0.11 valid=30s;

    location /ws {
        set $fastapi{PROJECT_NAME} {PROJECT_NAME}-fastapi;
        proxy_pass http://$fastapi{PROJECT_NAME}:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
        proxy_connect_timeout       300;
        proxy_send_timeout          300;
        proxy_read_timeout          300;
        send_timeout                300;
    }

    location / {
        set $fastapi{PROJECT_NAME} {PROJECT_NAME}-fastapi;
        proxy_pass http://$fastapi{PROJECT_NAME}:8080;
        proxy_buffer_size   128k;
        proxy_buffers   4 256k;
        proxy_busy_buffers_size   256k;
        proxy_connect_timeout       300;
        proxy_send_timeout          300;
        proxy_read_timeout          300;
        send_timeout                300;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto 'https';
    }
}
"""