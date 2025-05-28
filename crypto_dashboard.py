#!/usr/bin/env python
"""
Crypto Arbitrage Dashboard
Real-time monitoring of order books, funding rates, and large trades from Hyperliquid
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import time
import json
import threading
import queue
import logging
from datetime import datetime, timedelta
from hyperliquid.info import Info
import websocket
import traceback
import uuid

# Set page configuration
st.set_page_config(
    page_title="Crypto Arbitrage Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Enhanced WebSocket state management
class WebSocketState:
    def __init__(self):
        self.connected = False
        self.message_count = 0
        self.trade_count = 0
        self.orderbook_count = 0
        self.last_message_time = 0
        self.last_trade_time = 0
        self.ws_instance = None
        self.thread = None
        self.session_id = str(uuid.uuid4())[:8]
        self.connection_start_time = 0
        self.total_volume = 0.0
        
    def reset(self):
        self.connected = False
        self.message_count = 0
        self.trade_count = 0
        self.orderbook_count = 0
        self.last_message_time = 0
        self.last_trade_time = 0
        self.session_id = str(uuid.uuid4())[:8]
        self.connection_start_time = time.time()
        self.total_volume = 0.0
        
    def add_trade_volume(self, volume):
        self.total_volume += volume

# Initialize session state
def initialize_session_state():
    if 'ws_state' not in st.session_state:
        st.session_state.ws_state = WebSocketState()
    if 'trades' not in st.session_state:
        st.session_state.trades = []
    if 'order_books' not in st.session_state:
        st.session_state.order_books = {}
    if 'latest_prices' not in st.session_state:
        st.session_state.latest_prices = {}
    if 'funding_rates' not in st.session_state:
        st.session_state.funding_rates = {}
    if 'debug_messages' not in st.session_state:
        st.session_state.debug_messages = []
    if 'message_queue' not in st.session_state:
        st.session_state.message_queue = queue.Queue(maxsize=10000)
    if 'trade_stats' not in st.session_state:
        st.session_state.trade_stats = {'by_instrument': {}, 'by_side': {}, 'hourly': {}}
    if 'connection_history' not in st.session_state:
        st.session_state.connection_history = []
    if 'selected_instruments' not in st.session_state:
        st.session_state.selected_instruments = ["BTC", "ETH", "SOL"]
    if 'trade_threshold' not in st.session_state:
        st.session_state.trade_threshold = 1000

initialize_session_state()

# HTTP client for REST API calls
@st.cache_resource
def get_info_client():
    return Info(skip_ws=True)

# Enhanced debugging and logging
def add_debug_message(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    full_msg = f"[{level}] {timestamp}: {msg}"
    
    # Keep only recent messages
    if len(st.session_state.debug_messages) > 200:
        st.session_state.debug_messages = st.session_state.debug_messages[-100:]
    
    st.session_state.debug_messages.append(full_msg)
    
    # Also log to console
    if level == "ERROR":
        logger.error(msg)
    elif level == "WARN":
        logger.warning(msg)
    else:
        logger.info(msg)

# Enhanced WebSocket message handlers
def on_message(ws, message):
    """Handle incoming WebSocket messages with enhanced processing"""
    try:
        st.session_state.ws_state.message_count += 1
        st.session_state.ws_state.last_message_time = time.time()
        
        # Handle connection confirmation
        if message == "Websocket connection established.":
            add_debug_message("✅ WebSocket connection established")
            return
        
        # Parse JSON message
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            add_debug_message(f"❌ Non-JSON message: {message[:100]}", "WARN")
            return
        
        # Handle pong responses
        if data.get('channel') == 'pong':
            add_debug_message("🏓 Received pong response")
            return
        
        # Log first few messages for debugging
        if st.session_state.ws_state.message_count <= 5:
            add_debug_message(f"📨 Message {st.session_state.ws_state.message_count}: {data.get('channel', 'unknown')} channel")
        
        # Queue message for processing
        try:
            st.session_state.message_queue.put(data, block=False)
        except queue.Full:
            # Clear old messages if queue is full
            try:
                for _ in range(100):
                    st.session_state.message_queue.get_nowait()
                st.session_state.message_queue.put(data, block=False)
                add_debug_message("⚠️ Queue was full, cleared old messages", "WARN")
            except:
                add_debug_message("❌ Failed to clear queue, dropping message", "ERROR")
                
    except Exception as e:
        add_debug_message(f"❌ Error in on_message: {str(e)}", "ERROR")
        add_debug_message(traceback.format_exc(), "ERROR")

def on_error(ws, error):
    """Handle WebSocket errors"""
    add_debug_message(f"❌ WebSocket error: {str(error)}", "ERROR")
    st.session_state.ws_state.connected = False

def on_close(ws, close_status_code, close_msg):
    """Handle WebSocket connection close"""
    add_debug_message(f"🔴 WebSocket closed: {close_msg} (code: {close_status_code})", "WARN")
    st.session_state.ws_state.connected = False
    
    # Record disconnection in history
    st.session_state.connection_history.append({
        'time': datetime.now(),
        'event': 'disconnect',
        'message': f"{close_msg} (code: {close_status_code})"
    })

def on_open(ws):
    """Handle WebSocket connection open with enhanced subscription management"""
    add_debug_message("🎉 WebSocket connection opened!")
    st.session_state.ws_state.connected = True
    st.session_state.ws_state.connection_start_time = time.time()
    
    # Record connection in history
    st.session_state.connection_history.append({
        'time': datetime.now(),
        'event': 'connect',
        'message': f"Session {st.session_state.ws_state.session_id}"
    })
    
    # Subscribe to channels with error handling
    try:
        time.sleep(0.5)  # Brief pause to ensure connection is stable
        
        # Subscribe to allMids for price updates
        allmids_sub = {
            "method": "subscribe",
            "subscription": {"type": "allMids"}
        }
        ws.send(json.dumps(allmids_sub))
        add_debug_message("✅ Subscribed to allMids")
        
        # Subscribe to instrument-specific channels
        for instrument in st.session_state.selected_instruments:
            try:
                # Trades subscription
                trades_sub = {
                    "method": "subscribe",
                    "subscription": {"type": "trades", "coin": instrument}
                }
                ws.send(json.dumps(trades_sub))
                add_debug_message(f"✅ Subscribed to trades for {instrument}")
                time.sleep(0.1)  # Small delay between subscriptions
                
                # Order book subscription
                book_sub = {
                    "method": "subscribe",
                    "subscription": {"type": "l2Book", "coin": instrument}
                }
                ws.send(json.dumps(book_sub))
                add_debug_message(f"✅ Subscribed to l2Book for {instrument}")
                time.sleep(0.1)
                
            except Exception as e:
                add_debug_message(f"❌ Error subscribing to {instrument}: {str(e)}", "ERROR")
        
        add_debug_message("🚀 All subscriptions completed successfully!")
            
    except Exception as e:
        add_debug_message(f"❌ Critical error during subscription setup: {str(e)}", "ERROR")
        add_debug_message(traceback.format_exc(), "ERROR")

def start_websocket_thread():
    """Enhanced WebSocket thread management with better error handling"""
    def run_websocket():
        reconnect_attempts = 0
        max_reconnect_attempts = 5
        base_delay = 5
        
        while reconnect_attempts < max_reconnect_attempts:
            try:
                add_debug_message(f"🔄 Starting WebSocket (attempt {reconnect_attempts + 1}/{max_reconnect_attempts})")
                
                # Enable debugging for first few attempts
                if reconnect_attempts < 2:
                    websocket.enableTrace(True)
                
                # Create WebSocket with enhanced configuration
                ws = websocket.WebSocketApp(
                    "wss://api.hyperliquid.xyz/ws",
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close
                )
                
                st.session_state.ws_state.ws_instance = ws
                
                # Run WebSocket with automatic ping/pong
                ws.run_forever(
                    ping_interval=50,  # Send ping every 50 seconds
                    ping_timeout=10,   # Wait 10 seconds for pong
                    skip_utf8_validation=True
                )
                
                # If we get here, connection ended normally
                add_debug_message("WebSocket connection ended normally")
                break
                
            except Exception as e:
                reconnect_attempts += 1
                add_debug_message(f"❌ WebSocket thread error (attempt {reconnect_attempts}): {str(e)}", "ERROR")
                
                if reconnect_attempts < max_reconnect_attempts:
                    delay = base_delay * (2 ** (reconnect_attempts - 1))  # Exponential backoff
                    add_debug_message(f"⏳ Retrying in {delay} seconds...", "WARN")
                    time.sleep(delay)
                else:
                    add_debug_message(f"❌ Max reconnection attempts ({max_reconnect_attempts}) reached", "ERROR")
                    st.session_state.ws_state.connected = False
        
        add_debug_message("🔚 WebSocket thread ended")
    
    # Stop existing connection if running
    if st.session_state.ws_state.thread and st.session_state.ws_state.thread.is_alive():
        add_debug_message("🛑 Stopping existing WebSocket connection")
        if st.session_state.ws_state.ws_instance:
            st.session_state.ws_state.ws_instance.close()
        time.sleep(1)  # Allow time for cleanup
    
    # Reset state and start new connection
    st.session_state.ws_state.reset()
    thread = threading.Thread(target=run_websocket, daemon=True, name=f"WebSocket-{st.session_state.ws_state.session_id}")
    thread.start()
    st.session_state.ws_state.thread = thread
    
    return f"🚀 WebSocket thread started (Session: {st.session_state.ws_state.session_id})"

def process_websocket_messages():
    """Enhanced message processing with detailed statistics and error handling"""
    processed_count = 0
    trade_count = 0
    orderbook_count = 0
    price_updates = 0
    errors = 0
    
    start_time = time.time()
    
    # Process messages with time limit to prevent UI blocking
    while not st.session_state.message_queue.empty() and processed_count < 200 and (time.time() - start_time) < 1.0:
        try:
            message = st.session_state.message_queue.get_nowait()
            processed_count += 1
            
            channel = message.get('channel', 'unknown')
            
            # Process trade messages
            if channel == 'trades' and 'data' in message:
                trades = message['data']
                
                if not trades:  # Skip empty trade arrays
                    continue
                
                # Debug first few trade messages
                if st.session_state.ws_state.trade_count < 3:
                    add_debug_message(f"📈 Processing {len(trades)} trades from {trades[0].get('coin', 'unknown')}")
                
                for trade in trades:
                    instrument = trade.get('coin')
                    if instrument and instrument in st.session_state.selected_instruments:
                        try:
                            # Parse trade data with enhanced validation
                            side_code = trade.get('side', '')
                            side = "Buy" if side_code == "B" else "Sell" if side_code == "A" else "Unknown"
                            
                            # Validate required fields
                            sz_str = trade.get('sz', '0')
                            px_str = trade.get('px', '0')
                            
                            if not sz_str or not px_str:
                                continue
                            
                            size = float(sz_str)
                            price = float(px_str)
                            
                            if size <= 0 or price <= 0:
                                continue
                            
                            notional = size * price
                            
                            # Update trade statistics
                            st.session_state.ws_state.trade_count += 1
                            st.session_state.ws_state.last_trade_time = time.time()
                            st.session_state.ws_state.add_trade_volume(notional)
                            
                            # Update instrument-specific stats
                            if instrument not in st.session_state.trade_stats['by_instrument']:
                                st.session_state.trade_stats['by_instrument'][instrument] = {'count': 0, 'volume': 0.0}
                            
                            st.session_state.trade_stats['by_instrument'][instrument]['count'] += 1
                            st.session_state.trade_stats['by_instrument'][instrument]['volume'] += notional
                            
                            # Update side stats
                            if side not in st.session_state.trade_stats['by_side']:
                                st.session_state.trade_stats['by_side'][side] = {'count': 0, 'volume': 0.0}
                            
                            st.session_state.trade_stats['by_side'][side]['count'] += 1
                            st.session_state.trade_stats['by_side'][side]['volume'] += notional
                            
                            # Only record trades above threshold
                            if notional >= st.session_state.trade_threshold:
                                trade_count += 1
                                
                                # Parse timestamp
                                trade_timestamp = trade.get('time', time.time() * 1000)
                                try:
                                    trade_time = datetime.fromtimestamp(int(trade_timestamp) / 1000)
                                except:
                                    trade_time = datetime.now()
                                
                                # Create trade record
                                new_trade = {
                                    'instrument': instrument,
                                    'side': side,
                                    'size': size,
                                    'price': price,
                                    'notional': notional,
                                    'time': trade_time,
                                    'hash': trade.get('hash', ''),
                                    'tid': trade.get('tid', 0)
                                }
                                
                                # Add to trades list with size management
                                st.session_state.trades.append(new_trade)
                                
                                # Maintain reasonable list size
                                if len(st.session_state.trades) > 500:
                                    st.session_state.trades = st.session_state.trades[-250:]
                                
                                # Log significant trades
                                if trade_count <= 5 or notional > 10000:
                                    add_debug_message(f"💰 Large trade: {instrument} {side} {size:.4f} @ ${price:,.2f} = ${notional:,.0f}")
                                
                        except (ValueError, TypeError, KeyError) as e:
                            errors += 1
                            if errors <= 3:  # Only log first few errors to avoid spam
                                add_debug_message(f"❌ Error parsing trade data: {str(e)}", "WARN")
            
            # Process order book updates
            elif channel == 'l2Book' and 'data' in message:
                orderbook_count += 1
                data = message['data']
                instrument = data.get('coin')
                
                if instrument and instrument in st.session_state.selected_instruments:
                    # Validate order book structure
                    if 'levels' in data and len(data.get('levels', [])) >= 2:
                        st.session_state.order_books[instrument] = data
                        st.session_state.ws_state.orderbook_count += 1
                        
                        # Log first few order book updates
                        if st.session_state.ws_state.orderbook_count <= 3:
                            add_debug_message(f"📊 Updated order book for {instrument}")
            
            # Process price updates
            elif channel == 'allMids' and 'data' in message:
                price_updates += 1
                mids_data = message.get('data', {}).get('mids', {})
                
                updated_count = 0
                for coin, price_str in mids_data.items():
                    if coin in st.session_state.selected_instruments:
                        try:
                            price = float(price_str)
                            if price > 0:
                                st.session_state.latest_prices[coin] = price
                                updated_count += 1
                        except (ValueError, TypeError):
                            pass
                
                if price_updates <= 3 and updated_count > 0:
                    add_debug_message(f"💲 Updated {updated_count} prices")
            
            # Handle subscription confirmations and other messages
            elif 'result' in message or 'error' in message:
                if 'error' in message:
                    add_debug_message(f"❌ Subscription error: {message['error']}", "ERROR")
                # Don't log successful subscription confirmations to reduce noise
                
        except queue.Empty:
            break
        except Exception as e:
            errors += 1
            if errors <= 5:  # Limit error logging
                add_debug_message(f"❌ Error processing message: {str(e)}", "ERROR")
    
    processing_time = time.time() - start_time
    
    # Create detailed processing summary
    summary_parts = []
    if processed_count > 0:
        summary_parts.append(f"{processed_count} msgs")
    if trade_count > 0:
        summary_parts.append(f"{trade_count} trades")
    if orderbook_count > 0:
        summary_parts.append(f"{orderbook_count} books")
    if price_updates > 0:
        summary_parts.append(f"{price_updates} prices")
    if errors > 0:
        summary_parts.append(f"{errors} errors")
    
    summary = f"Processed: {', '.join(summary_parts) if summary_parts else 'no messages'} ({processing_time:.3f}s)"
    
    # Log processing summary periodically
    if processed_count > 50 or trade_count > 0 or errors > 0:
        add_debug_message(f"📊 {summary}")
    
    return {
        'processed_count': processed_count,
        'trade_count': trade_count,
        'orderbook_count': orderbook_count,
        'price_updates': price_updates,
        'errors': errors,
        'processing_time': processing_time,
        'summary': summary
    }

# Enhanced REST API functions with better caching and error handling
@st.cache_data(ttl=30, show_spinner=False)
def get_order_book(instrument):
    """Get order book via REST API with enhanced error handling"""
    try:
        client = get_info_client()
        result = client.l2_snapshot(instrument)
        
        if result and 'levels' in result:
            return result
        else:
            add_debug_message(f"⚠️ Invalid order book format for {instrument}", "WARN")
            return None
            
    except Exception as e:
        add_debug_message(f"❌ Error fetching order book for {instrument}: {str(e)}", "ERROR")
        return None

@st.cache_data(ttl=120, show_spinner=False)  # Cache funding rates longer
def get_funding_rate(instrument):
    """Get funding rate via REST API with enhanced calculation"""
    try:
        client = get_info_client()
        now = int(time.time() * 1000)
        # Get more funding history for better accuracy
        funding_history = client.funding_history(instrument, now - 6 * 60 * 60 * 1000, now)
        
        if not funding_history:
            add_debug_message(f"⚠️ No funding history for {instrument}", "WARN")
            return None
        
        # Use the most recent funding rate
        latest_funding = funding_history[0]
        funding_time = latest_funding['time'] / 1000
        
        # Calculate next funding time (every 8 hours at 00:00, 08:00, 16:00 UTC)
        current_hour = datetime.fromtimestamp(time.time()).hour
        hours_since_last = current_hour % 8
        hours_to_next = 8 - hours_since_last if hours_since_last != 0 else 8
        next_funding_time = time.time() + (hours_to_next * 3600)
        
        # Calculate average funding rate from recent history for stability
        recent_rates = [float(entry['fundingRate']) for entry in funding_history[:3]]
        avg_rate = sum(recent_rates) / len(recent_rates)
        
        return {
            'instrument': instrument,
            'rate': float(latest_funding['fundingRate']),
            'avg_rate': avg_rate,
            'premium': float(latest_funding.get('premium', 0)),
            'next_funding_time': next_funding_time,
            'timestamp': latest_funding['time'],
            'history_count': len(funding_history)
        }
        
    except Exception as e:
        add_debug_message(f"❌ Error fetching funding rate for {instrument}: {str(e)}", "ERROR")
        return None

@st.cache_data(ttl=60, show_spinner=False)
def get_all_prices():
    """Get all current prices via REST API"""
    try:
        client = get_info_client()
        return client.all_mids()
    except Exception as e:
        add_debug_message(f"❌ Error fetching prices: {str(e)}", "ERROR")
        return {}

# Enhanced UI Components
def render_connection_status():
    """Render detailed connection status in sidebar"""
    st.sidebar.subheader("🔗 Connection Status")
    
    status = st.session_state.ws_state
    
    if status.connected:
        st.sidebar.success("✅ Connected")
        
        # Connection details
        uptime = time.time() - status.connection_start_time if status.connection_start_time > 0 else 0
        st.sidebar.write(f"**Session:** {status.session_id}")
        st.sidebar.write(f"**Uptime:** {uptime:.0f}s")
        st.sidebar.write(f"**Messages:** {status.message_count:,}")
        st.sidebar.write(f"**Trades:** {status.trade_count:,}")
        st.sidebar.write(f"**Volume:** ${status.total_volume:,.0f}")
        st.sidebar.write(f"**Queue:** {st.session_state.message_queue.qsize()}")
        
        # Last activity
        if status.last_message_time > 0:
            last_msg_ago = time.time() - status.last_message_time
            color = "🟢" if last_msg_ago < 10 else "🟡" if last_msg_ago < 30 else "🔴"
            st.sidebar.write(f"**Last Message:** {color} {last_msg_ago:.1f}s ago")
        
        if status.last_trade_time > 0:
            last_trade_ago = time.time() - status.last_trade_time
            st.sidebar.write(f"**Last Trade:** {last_trade_ago:.1f}s ago")
        
        # Disconnect button
        if st.sidebar.button("🔌 Disconnect", type="secondary"):
            if status.ws_instance:
                status.ws_instance.close()
            status.connected = False
            add_debug_message("🔌 Manual disconnect requested")
            st.rerun()
            
    else:
        st.sidebar.error("❌ Disconnected")
        
        # Connection controls
        col1, col2 = st.sidebar.columns(2)
        
        with col1:
            if st.button("🔗 Connect", type="primary"):
                result = start_websocket_thread()  
                st.sidebar.info(result)
                time.sleep(1)
                st.rerun()
        
        with col2:
            if st.button("🔄 Retry", type="secondary"):
                # Force restart
                if status.ws_instance:
                    status.ws_instance.close()
                time.sleep(0.5)
                result = start_websocket_thread()
                st.sidebar.info("Retrying...")
                time.sleep(1)
                st.rerun()

def render_trade_metrics():
    """Render enhanced trade metrics"""
    trades_df = pd.DataFrame(st.session_state.trades) if st.session_state.trades else pd.DataFrame()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        total_trades = len(trades_df)
        st.metric("📊 Total Trades", f"{total_trades:,}")
    
    with col2:
        if not trades_df.empty:
            total_volume = trades_df['notional'].sum()
            st.metric("💰 Total Volume", f"${total_volume:,.0f}")
        else:
            st.metric("💰 Total Volume", "$0")
    
    with col3:
        threshold = st.session_state.trade_threshold
        st.metric("🎯 Threshold", f"${threshold:,}")
    
    with col4:
        if not trades_df.empty:
            latest_trade = trades_df.iloc[-1]
            delta_color = "normal" if latest_trade['side'] == 'Buy' else "inverse"
            st.metric(
                "🕐 Latest Trade", 
                f"{latest_trade['instrument']}", 
                f"${latest_trade['notional']:,.0f}",
                delta_color=delta_color
            )
        else:
            st.metric("🕐 Latest Trade", "None")

def render_enhanced_trade_table(trades_df, max_rows=100):
    """Render enhanced trade table with better formatting"""
    if trades_df.empty:
        st.info("🔍 No trades to display. Lower the threshold or wait for more activity.")
        return
    
    # Prepare display data
    display_df = trades_df.head(max_rows).copy()
    display_df = display_df.sort_values('time', ascending=False)
    
    # Format columns
    display_df['time_str'] = display_df['time'].dt.strftime('%H:%M:%S')
    display_df['price_str'] = display_df['price'].apply(lambda x: f"${x:,.4f}")
    display_df['size_str'] = display_df['size'].apply(lambda x: f"{x:.6f}")
    display_df['notional_str'] = display_df['notional'].apply(lambda x: f"${x:,.0f}")
    
    # Add visual indicators
    display_df['side_icon'] = display_df['side'].apply(lambda x: "🟢 Buy" if x == "Buy" else "🔴 Sell")
    
    # Create the display table
    table_columns = ['time_str', 'instrument', 'side_icon', 'price_str', 'size_str', 'notional_str']
    column_names = ['Time', 'Instrument', 'Side', 'Price', 'Size', 'Notional']
    
    final_df = display_df[table_columns].copy()
    final_df.columns = column_names
    
    # Display with custom styling
    st.dataframe(
        final_df,
        use_container_width=True,
        height=min(400, len(final_df) * 35 + 50),
        hide_index=True
    )
    
    # Trade statistics
    if len(trades_df) > 1:
        st.subheader("📈 Trade Analysis")
        
        col1, col2 = st.columns(2)
        
        with col1:
            # Volume by instrument
            instrument_volume = trades_df.groupby('instrument')['notional'].agg(['sum', 'count']).reset_index()
            instrument_volume.columns = ['Instrument', 'Volume', 'Count']
            instrument_volume = instrument_volume.sort_values('Volume', ascending=False)
            
            st.write("**Volume by Instrument**")
            st.dataframe(
                instrument_volume.style.format({
                    'Volume': '${:,.0f}',
                    'Count': '{:,}'
                }),
                use_container_width=True
            )
        
        with col2:
            # Side analysis
            side_analysis = trades_df.groupby('side')['notional'].agg(['sum', 'count', 'mean']).reset_index()
            side_analysis.columns = ['Side', 'Total Volume', 'Trade Count', 'Avg Size']
            
            st.write("**Buy vs Sell Analysis**")
            st.dataframe(
                side_analysis.style.format({
                    'Total Volume': '${:,.0f}',
                    'Trade Count': '{:,}',
                    'Avg Size': '${:,.0f}'
                }),
                use_container_width=True
            )

def render_order_book_widget(instrument, order_book_data):
    """Render a compact order book widget"""
    if not order_book_data or 'levels' not in order_book_data or len(order_book_data['levels']) < 2:
        st.warning(f"No order book data for {instrument}")
        return
    
    bids = order_book_data['levels'][0][:5]  # Top 5 bids
    asks = order_book_data['levels'][1][:5]  # Top 5 asks
    
    if not bids or not asks:
        st.warning(f"Incomplete order book data for {instrument}")
        return
    
    # Calculate mid price and spread
    top_bid = float(bids[0]['px'])
    top_ask = float(asks[0]['px'])
    mid_price = (top_bid + top_ask) / 2
    spread = top_ask - top_bid
    spread_bps = (spread / mid_price) * 10000
    
    # Header with key metrics
    st.markdown(f"""
    <div style='background: #1f2937; padding: 8px 15px; border-radius: 6px; margin: 5px 0;
                display: flex; justify-content: space-between; align-items: center;'>
        <h4 style='color: #f3f4f6; margin: 0; font-size: 1.1em;'>{instrument}</h4>
        <div style='color: #9ca3af; font-size: 0.85em;'>
            <span style='color: #22c55e;'>Bid: ${top_bid:.4f}</span> | 
            <span style='color: #ef4444;'>Ask: ${top_ask:.4f}</span> | 
            <span style='color: #f59e0b;'>Spread: {spread_bps:.2f}bps</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Create side-by-side order book
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**🔴 Asks (Sells)**")
        ask_df = pd.DataFrame(asks[::-1])  # Reverse to show highest first
        ask_df['px'] = pd.to_numeric(ask_df['px'])
        ask_df['sz'] = pd.to_numeric(ask_df['sz'])
        ask_df['total'] = ask_df['px'] * ask_df['sz']
        
        ask_display = ask_df[['px', 'sz', 'total']].copy()
        ask_display.columns = ['Price', 'Size', 'Total']
        
        st.dataframe(
            ask_display.style.format({
                'Price': '${:.4f}',
                'Size': '{:.1f}',
                'Total': '${:.0f}'
            }).background_gradient(subset=['Size'], cmap='Reds', vmin=0),
            use_container_width=True,
            height=200,
            hide_index=True
        )
    
    with col2:
        st.markdown("**🟢 Bids (Buys)**")
        bid_df = pd.DataFrame(bids)
        bid_df['px'] = pd.to_numeric(bid_df['px'])
        bid_df['sz'] = pd.to_numeric(bid_df['sz'])
        bid_df['total'] = bid_df['px'] * bid_df['sz']
        
        bid_display = bid_df[['px', 'sz', 'total']].copy()
        bid_display.columns = ['Price', 'Size', 'Total']
        
        st.dataframe(
            bid_display.style.format({
                'Price': '${:.4f}',
                'Size': '{:.1f}',
                'Total': '${:.0f}'
            }).background_gradient(subset=['Size'], cmap='Greens', vmin=0),
            use_container_width=True,
            height=200,
            hide_index=True
        )

def render_funding_rate_widget(instrument, funding_data):
    """Render a compact funding rate widget"""
    if not funding_data:
        st.warning(f"No funding data for {instrument}")
        return
    
    rate = funding_data['rate']
    annualized = rate * 3 * 365 * 100  # 3 funding events per day
    next_funding = datetime.fromtimestamp(funding_data['next_funding_time'])
    time_to_next = (next_funding - datetime.now()).total_seconds() / 3600
    
    # Color coding based on rate
    rate_color = "#22c55e" if rate > 0 else "#ef4444" if rate < 0 else "#9ca3af"
    
    st.markdown(f"""
    <div style='background: #1f2937; padding: 12px 15px; border-radius: 6px; margin: 5px 0;'>
        <h4 style='color: #f3f4f6; margin: 0 0 8px 0; font-size: 1.1em;'>{instrument} Funding</h4>
        <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 15px; font-size: 0.9em;'>
            <div>
                <div style='color: #9ca3af; margin-bottom: 4px;'>Current Rate</div>
                <div style='color: {rate_color}; font-weight: bold; font-size: 1.1em;'>{rate:.6f}%</div>
            </div>
            <div>
                <div style='color: #9ca3af; margin-bottom: 4px;'>Annualized</div>
                <div style='color: {rate_color}; font-weight: bold; font-size: 1.1em;'>{annualized:.2f}%</div>
            </div>
            <div>
                <div style='color: #9ca3af; margin-bottom: 4px;'>Next Funding</div>
                <div style='color: #f3f4f6;'>{next_funding.strftime('%H:%M:%S')}</div>
            </div>
            <div>
                <div style='color: #9ca3af; margin-bottom: 4px;'>Time Remaining</div>
                <div style='color: #f3f4f6;'>{time_to_next:.1f}h</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# Main Application Layout
st.title("🚀 Crypto Arbitrage Dashboard")
st.markdown("**Real-time monitoring of order books, funding rates, and large trades from Hyperliquid**")

# Enhanced Sidebar
st.sidebar.title("⚙️ Dashboard Controls")

# Instrument selection with better organization
st.sidebar.subheader("🎯 Instruments")
available_instruments = ["BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE", "ARB", "OP", "MATIC", "UNI"]
st.session_state.selected_instruments = st.sidebar.multiselect(
    "Select instruments to monitor",
    available_instruments,
    default=st.session_state.selected_instruments,
    help="Choose which cryptocurrency perpetual futures to monitor"
)

# Trade threshold setting
st.sidebar.subheader("📊 Trade Settings")
st.session_state.trade_threshold = st.sidebar.slider(
    "Trade Threshold ($)",
    min_value=100,
    max_value=100000,
    value=st.session_state.trade_threshold,
    step=500,
    help="Only show trades larger than this amount"
)

# Auto-refresh settings
st.sidebar.subheader("🔄 Refresh Settings")
auto_refresh = st.sidebar.checkbox("Auto-refresh dashboard", value=True)
refresh_interval = st.sidebar.slider(
    "Refresh interval (seconds)",
    min_value=1,
    max_value=30,
    value=5,
    help="How often to refresh the dashboard"
)

# Connection status
render_connection_status()

# Debug toggle
show_debug = st.sidebar.checkbox("Show debug info", value=False)

# Clear data button
if st.sidebar.button("🗑️ Clear All Data", type="secondary"):
    st.session_state.trades = []
    st.session_state.order_books = {}
    st.session_state.latest_prices = {}
    st.session_state.debug_messages = []
    st.session_state.trade_stats = {'by_instrument': {}, 'by_side': {}, 'hourly': {}}
    add_debug_message("🗑️ All data cleared by user")
    st.rerun()

# Main content area
if not st.session_state.selected_instruments:
    st.warning("⚠️ Please select at least one instrument from the sidebar to begin monitoring.")
    st.stop()

# Create tabs for different views
tab1, tab2, tab3, tab4 = st.tabs(["📊 Order Books", "💰 Funding Rates", "📈 Live Trades", "🔧 Debug"])

# Tab 1: Order Books
with tab1:
    st.header("📊 Real-time Order Books")
    
    # Update button and status
    col1, col2 = st.columns([3, 1])
    with col1:
        st.write(f"Monitoring {len(st.session_state.selected_instruments)} instruments")
    with col2:
        if st.button("🔄 Refresh", key="refresh_orderbooks"):
            # Force refresh order books via REST API
            for instrument in st.session_state.selected_instruments:
                order_book = get_order_book(instrument)
                if order_book:
                    st.session_state.order_books[instrument] = order_book
            st.success("Order books refreshed!")
    
    # Display order books in a grid
    if len(st.session_state.selected_instruments) <= 3:
        columns = st.columns(len(st.session_state.selected_instruments))
        for i, instrument in enumerate(st.session_state.selected_instruments):
            with columns[i]:
                order_book_data = st.session_state.order_books.get(instrument)
                render_order_book_widget(instrument, order_book_data)
    else:
        # For more than 3 instruments, use a 2-column layout
        col1, col2 = st.columns(2)
        for i, instrument in enumerate(st.session_state.selected_instruments):
            with col1 if i % 2 == 0 else col2:
                order_book_data = st.session_state.order_books.get(instrument)
                render_order_book_widget(instrument, order_book_data)

# Tab 2: Funding Rates
with tab2:
    st.header("💰 Funding Rate Analysis")
    
    # Update button
    if st.button("🔄 Refresh Funding Rates", key="refresh_funding"):
        funding_data = []
        for instrument in st.session_state.selected_instruments:
            funding = get_funding_rate(instrument)
            if funding:
                st.session_state.funding_rates[instrument] = funding
        st.success("Funding rates refreshed!")
    
    # Display funding rates
    if len(st.session_state.selected_instruments) <= 2:
        columns = st.columns(len(st.session_state.selected_instruments))
        for i, instrument in enumerate(st.session_state.selected_instruments):
            with columns[i]:
                funding_data = st.session_state.funding_rates.get(instrument)
                render_funding_rate_widget(instrument, funding_data)
    else:
        col1, col2 = st.columns(2)
        for i, instrument in enumerate(st.session_state.selected_instruments):
            with col1 if i % 2 == 0 else col2:
                funding_data = st.session_state.funding_rates.get(instrument)
                render_funding_rate_widget(instrument, funding_data)
    
    # Funding rate comparison chart
    if st.session_state.funding_rates:
        st.subheader("📊 Funding Rate Comparison")
        
        # Prepare data for chart
        chart_data = []
        for instrument, data in st.session_state.funding_rates.items():
            if instrument in st.session_state.selected_instruments:
                chart_data.append({
                    'Instrument': instrument,
                    'Rate (%)': data['rate'] * 100,
                    'Annualized (%)': data['rate'] * 3 * 365 * 100
                })
        
        if chart_data:
            chart_df = pd.DataFrame(chart_data)
            
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=chart_df['Instrument'],
                y=chart_df['Annualized (%)'],
                name='Annualized Funding Rate',
                marker_color=px.colors.qualitative.Set3
            ))
            
            fig.update_layout(
                title="Annualized Funding Rates by Instrument",
                xaxis_title="Instrument",
                yaxis_title="Annualized Rate (%)",
                height=400,
                template="plotly_dark"
            )
            
            st.plotly_chart(fig, use_container_width=True)

# Tab 3: Live Trades
with tab3:
    st.header("📈 Live Trade Monitor")
    
    # Trade metrics
    render_trade_metrics()
    
    # Processing status
    processing_result = process_websocket_messages()
    
    if processing_result['processed_count'] > 0:
        st.success(f"✅ {processing_result['summary']}")
    elif st.session_state.ws_state.connected:
        st.info("🔄 Connected and waiting for trade data...")
    else:
        st.warning("⚠️ Not connected to live data feed. Click 'Connect' in the sidebar.")
    
    # Trade table
    trades_df = pd.DataFrame(st.session_state.trades) if st.session_state.trades else pd.DataFrame()
    
    if not trades_df.empty:
        st.subheader(f"Recent Large Trades (>${st.session_state.trade_threshold:,}+)")
        render_enhanced_trade_table(trades_df)
        
        # Trade visualizations
        if len(trades_df) >= 5:
            st.subheader("📊 Trade Visualizations")
            
            col1, col2 = st.columns(2)
            
            with col1:
                # Volume by instrument pie chart
                instrument_volume = trades_df.groupby('instrument')['notional'].sum().reset_index()
                
                fig = go.Figure(data=[go.Pie(
                    labels=instrument_volume['instrument'],
                    values=instrument_volume['notional'],
                    hole=0.3,
                    title="Volume Distribution by Instrument"
                )])
                
                fig.update_layout(height=400, template="plotly_dark")
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                # Buy vs Sell volume
                side_volume = trades_df.groupby('side')['notional'].sum().reset_index()
                
                colors = ['#22c55e' if side == 'Buy' else '#ef4444' for side in side_volume['side']]
                
                fig = go.Figure(data=[go.Pie(
                    labels=side_volume['side'],
                    values=side_volume['notional'],
                    hole=0.3,
                    marker_colors=colors,
                    title="Buy vs Sell Volume"
                )])
                
                fig.update_layout(height=400, template="plotly_dark")
                st.plotly_chart(fig, use_container_width=True)
            
            # Trade timeline
            if len(trades_df) >= 10:
                st.subheader("⏰ Trade Timeline")
                
                # Resample trades by minute for better visualization
                trades_df_copy = trades_df.copy()
                trades_df_copy.set_index('time', inplace=True)
                
                # Group by 1-minute intervals
                timeline_data = trades_df_copy.resample('1T').agg({
                    'notional': 'sum',
                    'instrument': 'count'
                }).reset_index()
                
                timeline_data = timeline_data[timeline_data['notional'] > 0]  # Remove empty intervals
                
                if not timeline_data.empty:
                    fig = make_subplots(
                        rows=2, cols=1,
                        subplot_titles=('Trade Volume Over Time', 'Trade Count Over Time'),
                        vertical_spacing=0.1
                    )
                    
                    # Volume timeline
                    fig.add_trace(
                        go.Scatter(
                            x=timeline_data['time'],
                            y=timeline_data['notional'],
                            mode='lines+markers',
                            name='Volume ($)',
                            line=dict(color='#3b82f6', width=2)
                        ),
                        row=1, col=1
                    )
                    
                    # Count timeline
                    fig.add_trace(
                        go.Bar(
                            x=timeline_data['time'],
                            y=timeline_data['instrument'],
                            name='Trade Count',
                            marker_color='#10b981'
                        ),
                        row=2, col=1
                    )
                    
                    fig.update_layout(
                        height=500,
                        template="plotly_dark",
                        showlegend=False
                    )
                    
                    fig.update_xaxes(title_text="Time", row=2, col=1)
                    fig.update_yaxes(title_text="Volume ($)", row=1, col=1)
                    fig.update_yaxes(title_text="Count", row=2, col=1)
                    
                    st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(f"🔍 No trades above ${st.session_state.trade_threshold:,} threshold yet. Lower the threshold or wait for larger trades.")

# Tab 4: Debug
with tab4:
    st.header("🔧 Debug Information")
    
    if show_debug:
        # WebSocket state details
        st.subheader("🔗 WebSocket State")
        
        status = st.session_state.ws_state
        debug_cols = st.columns(4)
        
        with debug_cols[0]:
            st.metric("Connected", "✅ Yes" if status.connected else "❌ No")
            st.metric("Session ID", status.session_id)
        
        with debug_cols[1]:
            st.metric("Messages", f"{status.message_count:,}")
            st.metric("Trades", f"{status.trade_count:,}")
        
        with debug_cols[2]:
            st.metric("Order Books", f"{status.orderbook_count:,}")
            st.metric("Queue Size", f"{st.session_state.message_queue.qsize():,}")
        
        with debug_cols[3]:
            if status.connection_start_time > 0:
                uptime = time.time() - status.connection_start_time
                st.metric("Uptime", f"{uptime:.0f}s")
            else:
                st.metric("Uptime", "N/A")
            
            st.metric("Total Volume", f"${status.total_volume:,.0f}")
        
        # Connection history
        if st.session_state.connection_history:
            st.subheader("📜 Connection History")
            history_df = pd.DataFrame(st.session_state.connection_history)
            history_df['time'] = history_df['time'].dt.strftime('%H:%M:%S')
            
            # Color code events
            def color_event(event):
                if event == 'connect':
                    return 'background-color: #22c55e30'
                elif event == 'disconnect':
                    return 'background-color: #ef444430'
                else:
                    return ''
            
            styled_history = history_df.style.applymap(color_event, subset=['event'])
            st.dataframe(styled_history, use_container_width=True)
        
        # Debug messages
        st.subheader("🐛 Debug Messages")
        
        col1, col2 = st.columns([4, 1])
        
        with col1:
            if st.session_state.debug_messages:
                # Show latest 50 messages
                recent_messages = st.session_state.debug_messages[-50:]
                st.code('\n'.join(reversed(recent_messages)))
            else:
                st.info("No debug messages yet")
        
        with col2:
            if st.button("🔄 Refresh Debug", key="refresh_debug"):
                st.rerun()
            
            if st.button("🧹 Clear Messages", key="clear_debug"):
                st.session_state.debug_messages = []
                st.success("Debug messages cleared")
        
        # Trade statistics
        st.subheader("📊 Trade Statistics")
        
        if st.session_state.trade_stats['by_instrument']:
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**By Instrument:**")
                instrument_stats = pd.DataFrame.from_dict(
                    st.session_state.trade_stats['by_instrument'], 
                    orient='index'
                ).reset_index()
                instrument_stats.columns = ['Instrument', 'Count', 'Volume']
                instrument_stats['Volume'] = instrument_stats['Volume'].apply(lambda x: f"${x:,.0f}")
                st.dataframe(instrument_stats, use_container_width=True)
            
            with col2:
                st.write("**By Side:**")
                side_stats = pd.DataFrame.from_dict(
                    st.session_state.trade_stats['by_side'], 
                    orient='index'
                ).reset_index()
                side_stats.columns = ['Side', 'Count', 'Volume']
                side_stats['Volume'] = side_stats['Volume'].apply(lambda x: f"${x:,.0f}")
                st.dataframe(side_stats, use_container_width=True)
    else:
        st.info("Enable 'Show debug info' in the sidebar to view detailed debug information.")

# Auto-start WebSocket connection if not connected
if not st.session_state.ws_state.connected and not st.session_state.ws_state.thread:
    add_debug_message("🚀 Auto-starting WebSocket connection")
    start_websocket_thread()

# Auto-refresh logic
if auto_refresh:
    # Process any pending WebSocket messages
    process_websocket_messages()
    
    # Sleep and refresh
    time.sleep(refresh_interval)
    st.rerun()
