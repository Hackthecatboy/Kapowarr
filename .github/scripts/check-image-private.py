"""Fail publishing if the development manifest is anonymously accessible."""

import json
import urllib.error
import urllib.request


def check():
    try:
        with urllib.request.urlopen(
            'https://ghcr.io/token?service=ghcr.io'
            '&scope=repository:hackthecatboy/kapowarr:pull', timeout=30
        ) as response:
            token = json.load(response)['token']
        request = urllib.request.Request(
            'https://ghcr.io/v2/hackthecatboy/kapowarr/manifests/dev',
            headers={
                'Authorization': 'Bearer ' + token,
                'Accept': 'application/vnd.oci.image.index.v1+json',
            },
        )
        with urllib.request.urlopen(request, timeout=30):
            raise SystemExit(
                'STOP: development image is anonymously accessible. '
                'Resolve package visibility before publishing more images.'
            )
    except urllib.error.HTTPError as error:
        if error.code not in (401, 403, 404):
            raise
        print('Anonymous access denied or image does not exist.')


if __name__ == '__main__':
    check()
