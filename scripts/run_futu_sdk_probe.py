from services.futu_account import FutuSdkClient


def main():
    client = FutuSdkClient()
    avail = client.availability()
    print(f"available={avail.available} message={avail.message}")
    if avail.available:
        try:
            accounts = client.list_accounts()
            print(f"accounts={len(accounts)}")
        except Exception as e:
            print(f"accounts_error={e}")


if __name__ == '__main__':
    main()
