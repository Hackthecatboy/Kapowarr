# Run the fork on Synology using local builds

This setup pulls `Hackthecatboy/Kapowarr` from GitHub, builds an image on the
NAS, and runs it using Docker Compose through Container Manager's Docker
engine. It does not publish images. Building still downloads base images and
dependencies from the internet.

Target: DS2422+ (`linux/amd64`), with Container Manager installed. The provided
Dockerfile avoids BuildKit-only syntax. Actual build/startup must be verified
on the NAS; preparation on the development machine is not a NAS runtime test.

## 1. Check the NAS

Enable SSH in DSM and connect using your administrator account. Run:

```bash
uname -m
id
command -v git
sudo docker version
sudo docker compose version
```

Expected architecture is `x86_64`. If the final command is unavailable, try
`sudo docker-compose version`; the update script supports both commands.
If Git is missing, install Synology's Git Server package to obtain the Git
command. You do not need to create or host a repository in Git Server.

Choose a NAS user that will own the test files. Run `id USERNAME` to obtain
its numeric UID and primary GID. Do not use this development computer's IDs.

## 2. Create a checkout and configure storage

The following uses `/volume1/docker` as an example. Adjust it if your Docker
shared folder lives on another volume. Keep source and data in separate folders.

```bash
sudo git clone --branch feature/sonarr-integrations --single-branch \
  https://github.com/Hackthecatboy/Kapowarr.git \
  /volume1/docker/kapowarr-dev-src
cd /volume1/docker/kapowarr-dev-src
sudo cp .env.synology.example .env.synology
sudo vi .env.synology
```

Set `PUID` and `PGID` to the selected NAS account's numeric IDs. Leave port
5657 unless it is already occupied. Set `KAPOWARR_DATA_DIR` to an absolute
path outside the checkout, for example `/volume1/docker/kapowarr-dev-data`.
The env file is local and Git-ignored. Enter simple `KEY=value` assignments.

Create the test folders (adjust the base path to match your env file):

```bash
sudo mkdir -p /volume1/docker/kapowarr-dev-data/db \
  /volume1/docker/kapowarr-dev-data/logs \
  /volume1/docker/kapowarr-dev-data/downloads \
  /volume1/docker/kapowarr-dev-data/comics \
  /volume1/docker/kapowarr-dev-data/backups
```

Give the selected account read/write access to the **test comics folder** using
DSM permissions, or `sudo chown UID:GID /volume1/docker/kapowarr-dev-data/comics`
with the actual numeric IDs. DSM shared-folder ACLs must also permit access.
The existing container entrypoint sets ownership of db, logs, and downloads
when PUID is nonzero. Backups are created as root.

Use fresh test folders. Do not map the running original application's database
or live library: the fork migrates its database and can rename or move comics.

## 3. Validate, then build and launch

Run the read-only check first and verify the displayed paths and port:

```bash
sudo bash scripts/synology-update.sh --check
```

Then build the checked-out revision and start it:

```bash
sudo bash scripts/synology-update.sh --start
```

The first build can take several minutes. The script builds before stopping
the existing test service, backs up its database folder, recreates the service,
and checks HTTP health. Open `http://NAS-IP:5657`. In Kapowarr, use `/comics`
as the root folder and `/app/temp_downloads` for downloads.

The container is named by Compose under project `kapowarr-dev` and is visible
in Container Manager's Containers view. DSM versions may differ in whether
externally created Compose projects appear in the Projects view; the script
does not register a project through DSM's private APIs. Use the script for
builds and updates, and Container Manager for logs and container status.

For commands equivalent to the script's Compose invocation:

```bash
sudo docker compose --project-name kapowarr-dev --env-file .env.synology \
  -f compose.synology.yml ps
sudo docker compose --project-name kapowarr-dev --env-file .env.synology \
  -f compose.synology.yml logs --tail 100 kapowarr
```

Substitute `docker-compose` if that is the available command. Do not launch
the upstream `docker-compose.yml`: it uses the original published image.

## 4. Update manually

```bash
cd /volume1/docker/kapowarr-dev-src
sudo bash scripts/synology-update.sh --update
```

This fetches the development branch, accepts only a fast-forward update of a
clean checkout, re-reads the updated script, builds locally, stops the test
service, backs up `/app/db`, and starts the new build. There is no scheduled
update and no image publishing. Do not run two updates simultaneously.

If the build fails, the existing container stays running. If backup fails,
the script attempts to restart the existing container and aborts replacement.
If startup/health fails, it returns an error and leaves the database backup
under the host data folder's `backups/db-TIMESTAMP` directory. An HTTP health
check verifies the web server, not every application feature or NAS permission.

Backups cover the database folder, not comics/downloads. They are retained
until you remove them; monitor their disk usage. Keep copies of test media if
you want to undo rename/conversion changes. Database migrations are not
automatically reversible: restoring an older code revision may also require
restoring its corresponding database backup with the container stopped.

## Source references

- [Docker Compose build specification](https://docs.docker.com/reference/compose-file/build/)
- [Synology Container Manager Projects](https://kb.synology.com/en-us/DSM/help/ContainerManager/docker_project?version=7)
