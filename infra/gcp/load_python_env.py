from __future__ import annotations

import shlex

from deploy_config import get_deploy_env


def main() -> None:
    env = get_deploy_env()
    for key, value in sorted(env.items()):
        quoted = shlex.quote(value)
        print(f'if [[ -z "${{{key}:-}}" ]]; then export {key}={quoted}; fi')


if __name__ == "__main__":
    main()

