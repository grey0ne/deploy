# The build stage runs natively and cross-compiles for the target platform (linux/amd64 in
# production): no emulation on an arm64 developer machine.
FROM --platform=$BUILDPLATFORM golang:1.27.3-alpine AS build
ARG TARGETOS TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go test ./... \
    && CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags="-s -w" -o /proxy .

FROM alpine:3.23.2
# CA certificates for the Let's Encrypt API and https upstreams
RUN apk add --no-cache ca-certificates
COPY --from=build /proxy /usr/local/bin/proxy
EXPOSE 80 443
ENTRYPOINT ["/usr/local/bin/proxy"]
