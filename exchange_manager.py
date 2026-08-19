import ccxt

class ExchangeManager:
    def __init__(self):
        self.connected_exchanges = {}

    def connect_exchange(self, account_name, exchange_id, api_key, secret_key, passphrase=None):
        """
        Connects to a crypto exchange using CCXT.
        Supports multi-account indexing via account_name.
        """
        try:
            exchange_class = getattr(ccxt, exchange_id.lower())
            
            options = {
                'apiKey': api_key,
                'secret': secret_key,
                'enableRateLimit': True,
            }

            # Handle exchange specific options
            if exchange_id.lower() == 'bingx':
                options['options'] = {'defaultType': 'swap'}  # Set default type for BingX Perpetual Futures
            else:
                options['options'] = {'defaultType': 'swap'}

            if passphrase:
                options['password'] = passphrase

            exchange_instance = exchange_class(options)
            
            # Save connection with account alias
            self.connected_exchanges[account_name] = {
                "exchange_id": exchange_id,
                "instance": exchange_instance
            }
            
            return {"status": True, "message": f"Successfully connected to {account_name} ({exchange_id})"}

        except Exception as e:
            return {"status": False, "message": str(e)}

    def disconnect_exchange(self, account_name):
        """Removes a connected exchange account from active connections."""
        if account_name in self.connected_exchanges:
            del self.connected_exchanges[account_name]
            return {"status": True, "message": f"Disconnected {account_name} successfully."}
        return {"status": False, "message": "Account not found."}

    def get_exchange_instance(self, account_name):
        """Retrieves active exchange object using account alias."""
        account = self.connected_exchanges.get(account_name)
        if account:
            return account["instance"]
        return None