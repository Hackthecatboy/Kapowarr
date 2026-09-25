# GitHub development images on Synology

This unofficial personal development fork publishes public GHCR images.
No GitHub account or token is required to pull them. See the
[fork notice](../README.md) before using these experimental builds.

GitHub Actions builds `Dockerfile.synology` for the DS2422+ (`linux/amd64`) on
pushes to `feature/sonarr-integrations`, after the Python tests pass. Images go
to `ghcr.io/hackthecatboy/kapowarr:dev`, with a second `sha-FULL_COMMIT_ID` tag
for choosing a specific revision. Docker Hub is not used. Publishing an image
does not update the running NAS container.

## 1. Get the first image built

In your fork's **Actions** tab, enable workflows if GitHub has disabled them
for this fork. Push the commit containing `.github/workflows/container.yml` to
`feature/sonarr-integrations`. Open **Build Synology image** and wait for a green
run. If workflows were enabled after the push, push a new commit to that branch
to trigger the first run. This workflow does not have a manual Run button.

The workflow uses GitHub's automatic `GITHUB_TOKEN`; you do not need to add a
publishing token to repository secrets. If organization/account policy blocks
package writes, the Actions log will explain the permission failure.

Visit your GitHub profile's **Packages**, open **kapowarr**, and confirm the
`dev` tag is available.

## 2. Check the NAS and pull the image

Enable SSH in DSM and connect using your NAS administrator account. Run:

```bash
uname -m
id
sudo docker version
```

Expect `x86_64`. Choose the NAS account that will own the test files and run
`id USERNAME` for its UID and primary GID. These must be NAS IDs.

Pull the public image directly:

```bash
sudo docker pull ghcr.io/hackthecatboy/kapowarr:dev
```

No registry login is needed. Pre-pulling through SSH verifies that the NAS can
reach GHCR; you do not need GHCR search in Container Manager's Registry tab.

## 3. Create the Container Manager project

These are example paths; adjust `/volume1` if your Docker share is elsewhere.
In File Station create `/volume1/docker/kapowarr-dev` for the project and
`/volume1/docker/kapowarr-dev-data` with subfolders `db`, `logs`, `downloads`,
`comics`, and `backups`. Use fresh test directories, separate from your existing
Kapowarr database and library. The fork migrates its database on startup.

Copy `compose.synology.ghcr.yml` from this branch into the project directory as
`compose.yaml`. Copy `.env.synology.example` alongside it as `.env`. Set:

```dotenv
KAPOWARR_DATA_DIR=/volume1/docker/kapowarr-dev-data
KAPOWARR_PORT=5657
TZ=America/New_York
PUID=YOUR_NUMERIC_NAS_UID
PGID=YOUR_NUMERIC_NAS_GID
```

Replace the last two values with numbers. Give that account read/write
permission to the test comics directory through DSM. The entrypoint prepares
ownership of db, logs, and downloads; DSM shared-folder ACLs must allow access.

In **Container Manager → Project → Create**, use name **kapowarr-dev**, path
`/volume1/docker/kapowarr-dev`, and the existing `compose.yaml`. Build/start the
project. The image has already been pulled and this Compose file has no build
section. If the GUI cannot start the project, use the CLI fallback below
to inspect the error.

Check the container logs and health, then open `http://NAS-IP:5657`. Inside
Kapowarr select `/comics` as the root folder and `/app/temp_downloads` for
downloads. For external clients, first arrange shared download mounts and
[Remote Path Mapping](torrent-downloads.md); a container's localhost is not
another container or the NAS host.

CLI fallback from the project directory (use `docker-compose` if needed):

```bash
cd /volume1/docker/kapowarr-dev
sudo docker compose -p kapowarr-dev config --quiet
sudo docker compose -p kapowarr-dev up -d --no-build
sudo docker compose -p kapowarr-dev ps
sudo docker compose -p kapowarr-dev logs --tail 100 kapowarr
```

Externally started containers appear in Container Manager, but DSM may not
register externally created Compose projects in its Projects view.

## 4. Update deliberately

After a green GitHub build, pull the image through SSH, then stop the test
project in Container Manager. With it stopped, copy its entire `db` directory
to a new timestamped folder in `backups` using File Station. Recreate the
project's container using the newly pulled image (Project Build/rebuild),
then check health and logs. A restart alone does not replace its image.

If the pull fails, leave the existing container running. If backup fails,
restart the existing container and resolve that before replacement. Keep the
previous image revision and matching database backup: older code may need its
older database. Media/downloads are not covered by this database backup.

To select a particular build, set `KAPOWARR_IMAGE` in `.env` to the full
`ghcr.io/hackthecatboy/kapowarr:sha-FULL_COMMIT_ID` tag, pull that exact image,
and recreate. Do not run `scripts/synology-update.sh` for this project: that
script belongs to the separate local source-build setup.

## References

- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Publishing images with GitHub Actions](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [Synology Container Manager Projects](https://kb.synology.com/en-global/DSM/help/ContainerManager/docker_project?version=7)
