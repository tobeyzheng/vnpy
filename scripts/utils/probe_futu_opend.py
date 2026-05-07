from services.futu_opend import OpenDClient


def main() -> None:
    result = OpenDClient().probe()
    print(f"reachable={result.reachable} host={result.host} port={result.port} message={result.message}")


if __name__ == "__main__":
    main()
