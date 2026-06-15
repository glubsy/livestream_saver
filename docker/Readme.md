# Build the image:

```
docker buildx build -f ./docker/Containerfile -t livestream_saver:latest .
```
or now with podman
```sh
podman build -f ./docker/Containerfile -t livestream_saver:latest -t livestream_saver:$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml) .
```

For a quick local development loop, use [`dev.sh`](./dev.sh). It builds the image and then prompts for either a channel URL or a config section name before starting `monitor <url>` or `monitor -s <section>` in the `testing` tag with your config directory, `downloads` volume, and host networking.

# POT provider dependency

This project now expects the [bgutil-ytdlp POT provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) to run separately from the downloader container.

- If you use `docker compose`, the provider is started automatically as the `pot-provider` service.
- If you run the downloader container manually, you must start the POT provider yourself before starting the downloader container.

The downloader container reads the provider URL from `LSS_POT_PROVIDER_URL` and injects it into the `yt-dlp` extractor arguments automatically.
The Docker image installs the optional POT-related Python dependencies from `requirements-pot.txt`.

# Run in a container:

* Create a config directory, like `$HOME/.config/livestream_saver` and copy the following files into it (or make hard links):
  * `livestream_saver.cfg`
  * `ytdlp_config.json`
  * your netscape-formatted cookies as `cookies.txt`
  * a `.env` file (to override some optional variables)

Then you can mount that directory inside the container as `/root/.config/livestream_saver`.
Don't forget to mount your `downloads` directory where both logs and data will be output.

Before starting the downloader container manually, start the POT provider container.
One simple option is:

```sh
docker run --rm -d --net=host --name bgutil-pot-provider -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider:latest
```

Then point the downloader container at it with `LSS_POT_PROVIDER_URL`.
If the provider is published on the same Docker host, a common value is `http://host.docker.internal:4416`.
On Linux, if `host.docker.internal` is not available in your Docker setup, use the Docker host IP instead or run both containers on the same user-defined Docker network.

```docker
docker run --rm --mount type=bind,src="$(echo $HOME)/.config/livestream_saver",target="/root/.config/livestream_saver" --mount type=bind,src="./downloads",target="/downloads" --env-file="$(echo $HOME)/.config/livestream_saver/.env" -e LSS_POT_PROVIDER_URL="http://host.docker.internal:4416" --network=host livestream_saver:latest monitor https://www.youtube.com/@Gura
```

WARNING: it is best to comment out all `cookies` values in your `livestream_saver.cfg` file. A single path is already provided from the `LSS_COOKIES_FILE` environment variable! 
Although, you could still specify and use these values if you really wanted to, by pointing to a directory you control (ie. mounted and accessible) in the container (the config directory is a good example).


## Run modes other than "monitor":

By default `monitor` mode is called but you can specify your own command when running the container:

```docker
docker run --rm  --mount type=bind,src="$(echo $HOME)/.config/livestream_saver",target="/root/.config/livestream_saver" --mount type=bind,src="./downloads",target="/downloads" --env-file="$(echo $HOME)/.config/livestream_saver/.env" -e LSS_POT_PROVIDER_URL="http://host.docker.internal:4416" --network=host livestream_saver:latest download <URL> 
```

# Run multiple containers

Docker compose is useful to spawn multiple containers at once, one container per monitored channel, plus a shared POT provider service.
Edit the `docker-compose.yml` file, create the download directory and point to it, then run:

```
docker-compose -f ./docker/compose.yaml --env-file "$(echo $HOME)/.config/livestream_saver/.env" up 
```

## Podman

If you use Podman and the default rootless networking causes issues with the POT provider service, use the Podman override file:

```sh
podman-compose -f ./docker/compose.yaml -f ./docker/compose.podman.yaml --env-file "$(echo $HOME)/.config/livestream_saver/.env" up
```

This override switches the services to host networking and points the downloader containers to the POT provider at `http://127.0.0.1:4416`.

# Maintainer notes

As a memo, to push a new image to Docker Hub:

```sh
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
podman push localhost/livestream_saver:$VERSION docker://docker.io/glubsy/livestream_saver:$VERSION
podman push localhost/livestream_saver:latest docker://docker.io/glubsy/livestream_saver:latest
```

Inspect an image with
```sh
dive --source podman localhost/livestream_saver:latest
```

GitHub Actions now handles this automatically from `.github/workflows/docker-publish.yml`.
Set these repository secrets before enabling the workflow:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

Tag behavior:

- published GitHub Releases trigger the Docker publish workflow
- release tags like `v0.3.3` are published as `0.3.3`
- `latest` is updated from the same release build
- every publish also gets a short SHA tag for traceability
