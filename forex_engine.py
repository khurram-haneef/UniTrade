import ccxt
import os
import streamlit as st

# BingX Official Forex & Commodity Swap Symbols
FOREX_SYMBOLS = {
    'Gold': 'XAUT/USDT:USDT',
    'Brent Oil': 'NCCO1OILBRENT2USD/USDT:USDT',
    'Silver': 'NCCOXAG2USD/USDT:USDT'
}

class ForexEngine:
    def __init__(self, exchange_manager):
        self.exchange_mgr = exchange_manager

    def place_forex_order(self, account_alias, symbol_key, side, amount, margin_mode="isolated", leverage=20, sizing_type="cost", current_price=0.0, stop_loss=None, take_profit=None):
        """
        Executes Forex / Commodity trades via BingX in Hedge Mode.
        """
        if account_alias not in self.exchange_mgr.connected_exchanges:
            return {"status": False, "message": "Selected BingX account not connected."}

        acc_info = self.exchange_mgr.connected_exchanges[account_alias]
        exchange = acc_info["instance"]
        symbol = FOREX_SYMBOLS.get(symbol_key, symbol_key)

        # Quantity Calculation
        if sizing_type == "cost":
            total_position_usdt = amount * leverage
            quantity = total_position_usdt / current_price if current_price > 0 else amount
        else:
            quantity = amount

        try:
            pos_side = 'LONG' if side.lower() == 'buy' else 'SHORT'

            try:
                exchange.set_leverage(int(leverage), symbol, params={'side': pos_side})
            except Exception:
                pass

            params = {
                'productType': 'Swap',
                'positionSide': pos_side
            }

            if stop_loss and float(stop_loss) > 0:
                params['stopLoss'] = {'triggerPrice': float(stop_loss)}
            if take_profit and float(take_profit) > 0:
                params['takeProfit'] = {'triggerPrice': float(take_profit)}

            order = exchange.create_order(
                symbol=symbol,
                type="market",
                side=side.lower(),
                amount=float(quantity),
                params=params
            )
            return {"status": True, "message": f"BingX Forex {side.upper()} order executed for {symbol_key}!", "order_details": order}

        except Exception as e:
            return {"status": False, "message": f"Forex Order Error: {str(e)}"}

    def close_forex_position(self, account_alias, symbol_key, position_side, amount_percentage=100):
        """
        Guaranteed Close for BingX Hedge Mode using Direct Native Endpoint or Clean Parameters.
        """
        if account_alias not in self.exchange_mgr.connected_exchanges:
            return {"status": False, "message": "Account not connected."}

        acc_info = self.exchange_mgr.connected_exchanges[account_alias]
        exchange = acc_info["instance"]
        raw_symbol = FOREX_SYMBOLS.get(symbol_key, symbol_key)

        try:
            # 1. Fetch active positions from BingX
            positions = exchange.fetch_positions()
            active_pos = None

            clean_target_symbol = raw_symbol.split('/')[0].split(':')[0].replace('-', '').upper()

            for pos in positions:
                pos_sym_clean = pos['symbol'].split('/')[0].split(':')[0].replace('-', '').upper()
                pos_side_clean = str(pos.get('side', '')).upper()
                
                contracts_val = float(pos.get('contracts', 0) or 0)
                if contracts_val == 0 and 'info' in pos:
                    contracts_val = abs(float(pos['info'].get('positionAmt', 0) or 0))

                if pos_sym_clean == clean_target_symbol and pos_side_clean == position_side.upper() and contracts_val > 0:
                    active_pos = pos
                    active_pos['exact_contracts'] = contracts_val
                    break

            if not active_pos:
                for pos in positions:
                    pos_sym_clean = pos['symbol'].split('/')[0].split(':')[0].replace('-', '').upper()
                    contracts_val = float(pos.get('contracts', 0) or 0)
                    if contracts_val == 0 and 'info' in pos:
                        contracts_val = abs(float(pos['info'].get('positionAmt', 0) or 0))

                    if pos_sym_clean == clean_target_symbol and contracts_val > 0:
                        active_pos = pos
                        active_pos['exact_contracts'] = contracts_val
                        break

            if not active_pos:
                return {"status": False, "message": f"No active position found on BingX for {symbol_key} ({position_side})."}

            total_contracts = active_pos['exact_contracts']
            close_amount = float(total_contracts) * (float(amount_percentage) / 100.0)

            actual_side = active_pos.get('side', position_side).upper()
            close_order_side = 'SELL' if actual_side == 'LONG' else 'BUY'

            # --- APPROACH 1: Direct BingX Native API Call ---
            try:
                # BingX API Native Payload
                bingx_symbol = raw_symbol.replace('/USDT:USDT', '-USDT').replace('/', '-')
                if '-' not in bingx_symbol and 'USDT' in bingx_symbol:
                    bingx_symbol = bingx_symbol.replace('USDT', '-USDT')

                native_payload = {
                    'symbol': bingx_symbol,
                    'side': close_order_side,
                    'positionSide': actual_side,
                    'type': 'MARKET',
                    'quantity': float(close_amount),
                    'reduceOnly': 'true'
                }
                
                # Direct call to BingX Swap Trade API
                if hasattr(exchange, 'privatePostOpenApiSwapV2TradeOrder'):
                    res = exchange.privatePostOpenApiSwapV2TradeOrder(native_payload)
                    return {"status": True, "message": f"Successfully closed {actual_side} position for {symbol_key}!", "details": res}
            except Exception:
                pass

            # --- APPROACH 2: CCXT Fallback with Restructured Params ---
            params = {
                'productType': 'Swap',
                'positionSide': actual_side,
                'reduceOnly': True
            }

            order = exchange.create_order(
                symbol=raw_symbol,
                type="market",
                side=close_order_side.lower(),
                amount=close_amount,
                params=params
            )
            return {"status": True, "message": f"Closed {actual_side} position for {symbol_key}!", "details": order}

        except Exception as e:
            return {"status": False, "message": f"Close Error: {str(e)}"}

    def set_forex_sl_tp(self, account_alias, symbol_key, position_side, stop_loss=None, take_profit=None):
        """
        Attaches/Updates SL/TP to an existing Hedge Mode position.
        """
        if account_alias not in self.exchange_mgr.connected_exchanges:
            return {"status": False, "message": "Account not connected."}

        acc_info = self.exchange_mgr.connected_exchanges[account_alias]
        exchange = acc_info["instance"]
        symbol = FOREX_SYMBOLS.get(symbol_key, symbol_key)

        try:
            params = {
                'productType': 'Swap',
                'positionSide': position_side.upper()
            }
            
            if stop_loss and float(stop_loss) > 0:
                params['stopLoss'] = {'triggerPrice': float(stop_loss)}
            if take_profit and float(take_profit) > 0:
                params['takeProfit'] = {'triggerPrice': float(take_profit)}

            if 'stopLoss' in params or 'takeProfit' in params:
                res = exchange.create_order(
                    symbol=symbol,
                    type="market",
                    side='sell' if position_side.upper() == 'LONG' else 'buy',
                    amount=0.0,
                    params=params
                )
                return {"status": True, "message": f"SL/TP updated for {symbol_key}!", "details": res}
            
            return {"status": False, "message": "No SL or TP price provided."}

        except Exception as e:
            return {"status": False, "message": f"SL/TP Error: {str(e)}"}