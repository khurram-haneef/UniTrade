import ccxt

class OrderEngine:
    def __init__(self, exchange_manager):
        self.exchange_mgr = exchange_manager

    def set_futures_leverage_and_margin(self, exchange, symbol, margin_mode, leverage):
        try:
            ex_name = exchange.id.lower()
            if ex_name == "mexc":
                formatted_symbol = symbol.replace("/", "_").split(":")[0]
                m_type_val = 1 if margin_mode.lower() == "isolated" else 2
                try:
                    exchange.set_leverage(leverage, formatted_symbol)
                except Exception:
                    pass
                try:
                    exchange.set_margin_mode(m_type_val, formatted_symbol, params={'leverage': leverage})
                except Exception:
                    pass
            elif ex_name == "bingx":
                formatted_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                try:
                    exchange.set_leverage(leverage, formatted_symbol, params={'side': 'BOTH'})
                except Exception:
                    pass
            else:
                formatted_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                try:
                    exchange.set_margin_mode(margin_mode.lower(), formatted_symbol)
                except Exception:
                    pass
                try:
                    exchange.set_leverage(leverage, formatted_symbol)
                except Exception:
                    pass
        except Exception as e:
            print(f"Margin/Leverage Setup Error: {str(e)}")

    def place_futures_order(self, account_alias, symbol, side, amount, margin_mode="isolated", leverage=20, sizing_type="cost", current_price=0.0, stop_loss=None, take_profit=None):
        try:
            if account_alias not in self.exchange_mgr.connected_exchanges:
                return {"status": False, "message": "Account not found or connected."}

            acc_info = self.exchange_mgr.connected_exchanges[account_alias]
            exchange = acc_info["instance"]
            ex_name = acc_info["exchange_id"].lower()

            self.set_futures_leverage_and_margin(exchange, symbol, margin_mode, leverage)

            if sizing_type == "cost":
                total_position_usdt = amount * leverage
                quantity = total_position_usdt / current_price if current_price > 0 else 0.0
            else:
                quantity = amount

            params = {}

            if ex_name == "mexc":
                formatted_symbol = symbol.replace("/", "_").split(":")[0]
                pos_type = 1 if side.lower() == 'buy' else 2
                open_type_val = 1 if margin_mode.lower() == "isolated" else 2
                
                params['openType'] = open_type_val
                params['positionMode'] = pos_type
                params['leverage'] = int(leverage)
                
                if "BTC" in symbol:
                    contracts = int(quantity / 0.0001)
                    quantity = max(1, contracts)
                elif "ETH" in symbol:
                    contracts = int(quantity / 0.01)
                    quantity = max(1, contracts)
                else:
                    quantity = max(1, int(quantity))

                # Immediate SL/TP Attachment for MEXC
                if stop_loss and float(stop_loss) > 0:
                    params['stopLossPrice'] = round(float(stop_loss), 1)
                if take_profit and float(take_profit) > 0:
                    params['takeProfitPrice'] = round(float(take_profit), 1)

            elif ex_name == "bingx":
                formatted_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                params['productType'] = 'Swap'
                params['positionSide'] = 'BOTH'
                
                if stop_loss and float(stop_loss) > 0:
                    params['stopLoss'] = {'triggerPrice': float(stop_loss)}
                if take_profit and float(take_profit) > 0:
                    params['takeProfit'] = {'triggerPrice': float(take_profit)}

            else:
                formatted_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol

            order = exchange.create_order(
                symbol=formatted_symbol,
                type="market",
                side=side.lower(),
                amount=quantity,
                params=params
            )

            return {"status": True, "message": f"Futures {side.upper()} executed!", "order_details": order}

        except Exception as e:
            return {"status": False, "message": str(e)}

    def close_position(self, account_alias, symbol, side, amount, leverage=20):
        try:
            if account_alias not in self.exchange_mgr.connected_exchanges:
                return {"status": False, "message": "Account not connected."}

            acc_info = self.exchange_mgr.connected_exchanges[account_alias]
            exchange = acc_info["instance"]
            ex_name = acc_info["exchange_id"].lower()
            
            close_side = "sell" if side.lower() in ["long", "buy"] else "buy"
            params = {'reduceOnly': True}

            if ex_name == "mexc":
                formatted_symbol = symbol.replace("/", "_").split(":")[0]
                pos_type = 1 if side.lower() in ["long", "buy"] else 2
                params['openType'] = 1
                params['positionMode'] = pos_type
                params['leverage'] = int(leverage)
            else:
                formatted_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                if ex_name == "bingx":
                    params['productType'] = 'Swap'
                    params['positionSide'] = 'BOTH'

            order = exchange.create_order(
                symbol=formatted_symbol,
                type="market",
                side=close_side,
                amount=abs(float(amount)),
                params=params
            )
            return {"status": True, "message": f"Closed {symbol} position!", "order_details": order}
        except Exception as e:
            return {"status": False, "message": str(e)}

    def set_position_tpsl(self, account_alias, symbol, side, tp_price=None, sl_price=None, total_contracts=0, percentage=100, leverage=20):
        """
        Unified Position Adjustment Engine:
        Executes SL/TP via official CCXT endpoints with proper MEXC parameters.
        """
        try:
            if account_alias not in self.exchange_mgr.connected_exchanges:
                return {"status": False, "message": "Account not connected."}

            acc_info = self.exchange_mgr.connected_exchanges[account_alias]
            exchange = acc_info["instance"]
            ex_name = acc_info["exchange_id"].lower()
            
            close_side = "sell" if side.lower() in ["long", "buy"] else "buy"
            results = []

            raw_qty = float(total_contracts) * (float(percentage) / 100.0) if float(total_contracts) > 0 else 1.0

            if ex_name == "mexc":
                formatted_symbol = symbol.replace("/", "_").split(":")[0]
                pos_type_val = 1 if side.lower() in ["long", "buy", "1"] else 2
                qty = max(1, int(raw_qty))

                if tp_price and str(tp_price).strip() != "" and float(tp_price) > 0:
                    params_tp = {
                        'stopPrice': float(tp_price),
                        'triggerPrice': float(tp_price),
                        'planType': 1,
                        'openType': 1,
                        'positionMode': pos_type_val,
                        'positionType': pos_type_val,
                        'vol': qty,
                        'leverage': int(leverage)
                    }
                    try:
                        exchange.create_order(
                            symbol=formatted_symbol,
                            type="take_profit_market",
                            side=close_side,
                            amount=qty,
                            params=params_tp
                        )
                        results.append(f"TP: ${tp_price}")
                    except Exception as e_tp:
                        try:
                            exchange.private_post_contract_change_plan_order({
                                'symbol': formatted_symbol,
                                'takeProfitPrice': str(tp_price),
                                'positionType': pos_type_val
                            })
                            results.append(f"TP: ${tp_price}")
                        except Exception as ex_tp:
                            results.append(f"TP Error: {str(ex_tp)}")

                if sl_price and str(sl_price).strip() != "" and float(sl_price) > 0:
                    params_sl = {
                        'stopPrice': float(sl_price),
                        'triggerPrice': float(sl_price),
                        'planType': 2,
                        'openType': 1,
                        'positionMode': pos_type_val,
                        'positionType': pos_type_val,
                        'vol': qty,
                        'leverage': int(leverage)
                    }
                    try:
                        exchange.create_order(
                            symbol=formatted_symbol,
                            type="stop_market",
                            side=close_side,
                            amount=qty,
                            params=params_sl
                        )
                        results.append(f"SL: ${sl_price}")
                    except Exception as e_sl:
                        try:
                            exchange.private_post_contract_change_plan_order({
                                'symbol': formatted_symbol,
                                'stopLossPrice': str(sl_price),
                                'positionType': pos_type_val
                            })
                            results.append(f"SL: ${sl_price}")
                        except Exception as ex_sl:
                            results.append(f"SL Error: {str(ex_sl)}")

            else:
                formatted_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                qty = raw_qty
                
                if tp_price and float(tp_price) > 0:
                    params_tp = {'reduceOnly': True, 'stopPrice': float(tp_price)}
                    if ex_name == "bingx":
                        params_tp['productType'] = 'Swap'
                        params_tp['positionSide'] = 'BOTH'

                    exchange.create_order(
                        symbol=formatted_symbol,
                        type="take_profit_market",
                        side=close_side,
                        amount=qty,
                        params=params_tp
                    )
                    results.append(f"TP: ${tp_price}")

                if sl_price and float(sl_price) > 0:
                    params_sl = {'reduceOnly': True, 'stopPrice': float(sl_price)}
                    if ex_name == "bingx":
                        params_sl['productType'] = 'Swap'
                        params_sl['positionSide'] = 'BOTH'

                    exchange.create_order(
                        symbol=formatted_symbol,
                        type="stop_market",
                        side=close_side,
                        amount=qty,
                        params=params_sl
                    )
                    results.append(f"SL: ${sl_price}")

            return {"status": True, "message": f"TP/SL Updated ({', '.join(results)}) for {symbol}"}
        except Exception as e:
            return {"status": False, "message": f"TP/SL Error: {str(e)}"}

    def place_spot_order(self, account_alias, symbol, side, quantity):
        try:
            if account_alias not in self.exchange_mgr.connected_exchanges:
                return {"status": False, "message": "Account not connected."}

            acc_info = self.exchange_mgr.connected_exchanges[account_alias]
            exchange = acc_info["instance"]

            order = exchange.create_order(symbol=symbol, type="market", side=side.lower(), amount=quantity)
            return {"status": True, "message": f"Spot {side.upper()} order executed!", "order_details": order}
        except Exception as e:
            return {"status": False, "message": str(e)}

    def fetch_live_positions(self, account_alias):
        try:
            if account_alias not in self.exchange_mgr.connected_exchanges:
                return []

            acc_info = self.exchange_mgr.connected_exchanges[account_alias]
            exchange = acc_info["instance"]

            exchange.options['defaultType'] = 'swap'
            positions = exchange.fetch_positions()
            active_positions = []

            for p in positions:
                contracts = float(p.get('contracts', 0) or p.get('positionAmt', 0) or 0)
                if abs(contracts) > 0:
                    entry_price = float(p.get('entryPrice', 0) or 0)
                    mark_price = float(p.get('markPrice', 0) or p.get('lastPrice', 0) or entry_price)
                    unrealized_pnl = float(p.get('unrealizedPnl', 0) or 0)
                    leverage = float(p.get('leverage', 1) or 1)
                    
                    margin_used = (abs(contracts) * entry_price) / leverage if leverage > 0 else 1
                    pnl_percent = (unrealized_pnl / margin_used) * 100 if margin_used > 0 else 0.0

                    active_positions.append({
                        "Symbol": p.get('symbol', '').replace('_', '/').split(':')[0],
                        "Side": p.get('side', 'LONG' if contracts > 0 else 'SHORT').upper(),
                        "Contracts": abs(contracts),
                        "Leverage": f"{int(leverage)}x",
                        "EntryPrice": f"${entry_price:,.2f}",
                        "MarkPrice": f"${mark_price:,.2f}",
                        "LiquidationPrice": f"${float(p.get('liquidationPrice', 0) or 0):,.2f}",
                        "UnrealizedPnL": f"${unrealized_pnl:+.2f}",
                        "PnLPercent": f"{pnl_percent:+.2f}%"
                    })

            return active_positions
        except Exception as e:
            print(f"Fetch Positions Error: {str(e)}")
            return []