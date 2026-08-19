class WalletEngine:
    def __init__(self, exchange_manager):
        self.exchange_manager = exchange_manager

    def get_balances(self, account_name, wallet_type="futures"):
        """
        Fetches Spot or Futures wallet balances for a specific account.
        wallet_type: 'futures' or 'spot'
        """
        exchange = self.exchange_manager.get_exchange_instance(account_name)
        if not exchange:
            return {"status": False, "message": "Exchange instance not found."}

        try:
            # Set default type according to requested wallet
            if wallet_type == "futures":
                exchange.options['defaultType'] = 'swap'
                balance = exchange.fetch_balance(params={'type': 'swap'})
            else:
                exchange.options['defaultType'] = 'spot'
                balance = exchange.fetch_balance()
            
            usdt_info = balance.get('USDT', {})
            free_balance = usdt_info.get('free', 0.0)
            total_balance = usdt_info.get('total', 0.0)

            # Fallback handling for specific exchange response structures
            if free_balance == 0.0 and 'info' in balance:
                info_data = balance['info']
                if isinstance(info_data, dict) and 'data' in info_data:
                    for item in info_data['data']:
                        if item.get('currency') == 'USDT':
                            free_balance = float(item.get('availableBalance', item.get('free', 0.0)))
                            total_balance = float(item.get('equity', item.get('total', free_balance)))

            return {
                "status": True,
                "account_name": account_name,
                "wallet_type": wallet_type,
                "free_usdt": float(free_balance),
                "total_usdt": float(total_balance),
                "raw_balance": balance
            }

        except Exception as e:
            return {"status": False, "message": str(e)}