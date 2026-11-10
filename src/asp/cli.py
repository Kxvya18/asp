"""The `asp` command.

asp serve --port 5050     a recording AWS endpoint for programs in any language
                          (point them at it with AWS_ENDPOINT_URL=http://localhost:5050)
"""

from __future__ import annotations

import argparse
import time


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="asp", description="Least-privilege IAM from recorded AWS calls"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run a recording AWS endpoint (any language)")
    serve.add_argument("--port", type=int, default=5050)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--save-to", help="write the policy here when stopped (Ctrl+C)")
    args = parser.parse_args(argv)

    from asp.serve import Server

    server = Server(port=args.port, host=args.host).start()
    print(f"asp: recording AWS endpoint on {server.url}")
    print(f"     point your program at it:  AWS_ENDPOINT_URL={server.url}")
    print(f"     watch it live:             {server.url}/_asp/")
    print(f"     policy:                    {server.url}/_asp/policy   (reset: POST /_asp/reset)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if args.save_to:
            server.recorder.save(args.save_to)
            print(f"asp: policy written to {args.save_to}")
        server.stop()


if __name__ == "__main__":
    main()
