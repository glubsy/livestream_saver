# Build the image:

```
docker build -f ./docker/Dockerfile -t livestream_saver:latest
```

# Run in a container:

* Create a config directory, like `$HOME/.config/livestream_saver` and copy the following files into it (or make hard links):
  * `livestream_saver.cfg`
  * `ytdlp_config.json`
  * your netscape-formatted `cookies.txt`

Then you can mount that directory inside the container as `/root/.config/livestream_saver`.
Don't forget to mount your `downloads` directory where both logs and data will be output.

```docker
docker run --rm --mount type=bind,src="$(echo $HOME)/.config/livestream_saver",target="/root/.config/livestream_saver" --mount type=bind,src="./downloads",target="/downloads" livestream_saver:latest monitor -s Gura
```

WARNING: it is best to comment out all `cookies` values in your `livestream_saver.cfg` file. A single path is already provided from the `LSS_COOKIES_FILE` environment variable! 
Although, you could still specify and use these values if you really wanted to, by pointing to a directory you control (ie. mounted and accessible) in the container (the config directory is a good example).


## Run modes other than "monitor":

By default `monitor` mode is called but you can specify your own command when running the container:

```docker
docker run --rm  --mount type=bind,src="$(echo $HOME)/.config/livestream_saver",target="/root/.config/livestream_saver" --mount type=bind,src="./downloads",target="/downloads" livestream_saver:latest download <URL> 
```

# Run multiple containers

Docker compose is useful to spawn multiple containers at once, one container per monitored channel.
Edit the `docker-compose.yml` file, create the download directory and point to it, then run:

```
docker-compose -f ./docker/compose.yaml up
```