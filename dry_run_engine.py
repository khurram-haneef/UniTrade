import time

class DryRunEngine:
    def __init__(self, initial_virtual_balance=10000.0):
        self.is_active = False
        self.virtual_balance = initial_virtual_balance
        self.virtual_positions = []
        self.trade_history = []

    def toggle_demo_mode(self, status: bool):
        """Activates or Deactivates Dry Run Demo Mode."""
        self.is_active = status
        return f"Dry Run Mode set to: {self.is_active}"

    def execute_demo_order(self, symbol, side, amount, current_price, leverage=20):
        """
        Simulates order execution without hitting live exchange API.
        Tracks virtual positions and balance.
        """
        if not self.is_active:
            return {"status": False, "message": "Dry Run Mode is currently OFF."}

        position_value = amount * current_price
        required_margin = position_value / leverage

        if required_margin > self.virtual_balance:
            return {"status": False, "message": "Insufficient Virtual Balance for Demo Trade."}

        # Deduct margin from virtual balance
        self.virtual_balance -= required_margin

        position = {
            "id": int(time.time()),
            "symbol": symbol,
            "side": side,  # 'buy' (Long) or 'sell' (Short)
            "amount": amount,
            "entry_price": current_price,
            "leverage": leverage,
            "margin_used": required_margin,
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
        }

        self.virtual_positions.append(position)
        return {"status": True, "message": "Demo Order Executed Successfully.", "position": position}

    def close_demo_position(self, position_id, current_price):
        """Closes a virtual position and updates virtual balance with PnL."""
        for pos in self.virtual_positions:
            if pos["id"] == position_id:
                # Calculate PnL
                if pos["side"] == "buy":
                    pnl = (current_price - pos["entry_price"]) * pos["amount"]
                else:
                    pnl = (pos["entry_price"] - current_price) * pos["amount"]

                # Return margin + PnL to virtual balance
                self.virtual_balance += (pos["margin_used"] + pnl)
                
                pos["exit_price"] = current_price
                pos["pnl"] = pnl
                
                self.trade_history.append(pos)
                self.virtual_positions.remove(pos)

                return {"status": True, "message": "Demo Position Closed.", "pnl": pnl}

        return {"status": False, "message": "Position ID not found."}