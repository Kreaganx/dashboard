#!/usr/bin/env python
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import time
import json
import threading
import queue
import logging
from datetime import datetime
from hyperliquid.info import Info
import websocket
import traceback

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

# Global WebSocket state
class WebSocketState:
    def __init__(self):
        self.connected = False
        self.message_count = 0
        self.last_message_time = 0
        self.ws_instance = None
        self.thread = None
        
    def reset(self):
        self.connected = False
        self.message_count = 0
        self.last_message_time = 0

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
    if 'debug_messages' not in st.session_state:
        st.session_state.debug_messages = []
    if 'message_queue' not in st.session_state:
        st.session_state.message_queue = queue.Queue(maxsize=10000)

initialize_session_state()

# HTTP client for REST API calls
@st.cache_resource
def get_info_client():
    return Info(skip_ws=True)

# WebSocket message handlers
def add_debug_message(msg):
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    full_msg = f"{timestamp}: {msg}"
    
    if len(st.session_state.debug_messages) > 100:
        st.session_state.debug_messages = st.session_state.debug_messages[-50:]
    
    st.session_state.debug_messages.append(full_msg)
    print(full_msg)

def on_message(ws, message):
    """Handle incoming WebSocket messages"""
    try:
        st.session_state.ws_state.message_count += 1
        st.session_state.ws_state.last_message_time = time.time()
        
        # Handle connection confirmation
        if message == "Websocket connection established.":
            add_debug_message("WebSocket connection established")
            return
        
        # Parse JSON message
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            add_debug_message(f"Non-JSON message: {message[:100]}")
            return
        
        # Handle pong responses
        if data.get('channel') == 'pong':
            add_debug_message("Received pong response")
            return
        
        # Log first few messages for debugging
        if st.session_state.ws_state.message_count <= 10:
            add_debug_message(f"Message {st.session_state.ws_state.message_count}: {data.get('channel', 'unknown')} channel")
        
        # Queue message for processing
        try:
            st.session_state.message_queue.put(data, block=False)
        except queue.Full:
            # Clear old messages if queue is full
            try:
                for _ in range(100):
                    st.session_state.message_queue.get_nowait()
                st.session_state.message_queue.put(data, block=False)
                add_debug_message("Queue was full, cleared old messages")
            except:
                add_debug_message("Failed to clear queue, dropping message")
                
    except Exception as e:
        add_debug_message(f"Error in on_message: {str(e)}")

def on_error(ws, error):
    """Handle WebSocket errors"""
    add_debug_message(f"WebSocket error: {str(error)}")
    st.session_state.ws_state.connected = False

def on_close(ws, close_status_code, close_msg):
    """Handle WebSocket connection close"""
    add_debug_message(f"WebSocket closed: {close_msg} (code: {close_status_code})")
    st.session_state.ws_state.connected = False

def on_open(ws):
    """Handle WebSocket connection open"""
    add_debug_message("WebSocket connection opened!")
    st.session_state.ws_state.connected = True
    
    # Subscribe to channels
    try:
        # Subscribe to allMids
        allmids_sub = {
            "method": "subscribe",
            "subscription": {"type": "allMids"}
        }
        ws.send(json.dumps(allmids_sub))
        add_debug_message("Subscribed to allMids")
        
        # Subscribe to trades for each instrument
        for instrument in st.session_state.selected_instruments:
            trades_sub = {
                "method": "subscribe",
                "subscription": {"type": "trades", "coin": instrument}
            }
            ws.send(json.dumps(trades_sub))
            add_debug_message(f"Subscribed to trades for {instrument}")
            
            # Subscribe to order book
            book_sub = {
                "method": "subscribe",
                "subscription": {"type": "l2Book", "coin": instrument}
            }
            ws.send(json.dumps(book_sub))
            add_debug_message(f"Subscribed to l2Book for {instrument}")
            
    except Exception as e:
        add_debug_message(f"Error during subscription: {str(e)}")

def start_websocket_thread():
    """Start WebSocket in a background thread"""
    def run_websocket():
        try:
            add_debug_message("Starting WebSocket thread")
            
            # Enable debugging
            websocket.enableTrace(True)
            
            # Create WebSocket
            ws = websocket.WebSocketApp(
                "wss://api.hyperliquid.xyz/ws",
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            
            st.session_state.ws_state.ws_instance = ws
            
            # Run WebSocket
            ws.run_forever(ping_interval=60, ping_timeout=10)
            
        except Exception as e:
            add_debug_message(f"WebSocket thread error: {str(e)}")
            add_debug_message(traceback.format_exc())
    
    # Stop existing thread if running
    if st.session_state.ws_state.thread and st.session_state.ws_state.thread.is_alive():
        add_debug_message("Stopping existing WebSocket")
        if st.session_state.ws_state.ws_instance:
            st.session_state.ws_state.ws_instance.close()
    
    # Start new thread
    st.session_state.ws_state.reset()
    thread = threading.Thread(target=run_websocket, daemon=True)
    thread.start()
    st.session_state.ws_state.thread = thread
    
    return "WebSocket thread started"

def process_websocket_messages():
    """Process queued WebSocket messages"""
    processed_count = 0
    trade_count = 0
    
    while not st.session_state.message_queue.empty() and processed_count < 100:
        try:
            message = st.session_state.message_queue.get_nowait()
            processed_count += 1
            
            channel = message.get('channel', 'unknown')
            
            # Process trades
            if channel == 'trades' and 'data' in message:
                trades = message['data']
                
                for trade in trades:
                    instrument = trade.get('coin')
                    if instrument in st.session_state.selected_instruments:
                        trade_count += 1
                        
                        # Parse trade data
                        side_code = trade.get('side', '')
                        side = "Buy" if side_code == "B" else "Sell" if side_code == "A" else side_code
                        
                        try:
                            size = float(trade.get('sz', 0))
                            price = float(trade.get('px', 0))
                            notional = size * price
                            
                            # Only record trades above threshold
                            if notional >= st.session_state.trade_threshold:
                                trade_time = datetime.fromtimestamp(
                                    int(trade.get('time', time.time() * 1000)) / 1000
                                )
                                
                                # Add to trades list
                                new_trade = {
                                    'instrument': instrument,
                                    'side': side,
                                    'size': size,
                                    'price': price,
                                    'notional': notional,
                                    'time': trade_time
                                }
                                
                                st.session_state.trades.append(new_trade)
                                
                                # Keep only last 200 trades
                                if len(st.session_state.trades) > 200:
                                    st.session_state.trades = st.session_state.trades[-100:]
                                
                                add_debug_message(f"Large trade: {instrument} {side} ${notional:,.2f}")
                                
                        except (ValueError, TypeError) as e:
                            add_debug_message(f"Error parsing trade: {str(e)}")
            
            # Process order books
            elif channel == 'l2Book' and 'data' in message:
                data = message['data']
                instrument = data.get('coin')
                if instrument in st.session_state.selected_instruments:
                    st.session_state.order_books[instrument] = data
            
            # Process price updates
            elif channel == 'allMids' and 'data' in message:
                mids_data = message.get('data', {}).get('mids', {})
                for coin, price in mids_data.items():
                    if coin in st.session_state.selected_instruments:
                        try:
                            st.session_state.latest_prices[coin] = float(price)
                        except (ValueError, TypeError):
                            pass
                            
        except queue.Empty:
            break
        except Exception as e:
            add_debug_message(f"Error processing message: {str(e)}")
    
    return processed_count, trade_count

# REST API functions
@st.cache_data(ttl=30)
def get_order_book(instrument):
    """Get order book via REST API"""
    try:
        client = get_info_client()
        return client.l2_snapshot(instrument)
    except Exception as e:
        st.error(f"Error fetching order book for {instrument}: {str(e)}")
        return None

@st.cache_data(ttl=60)
def get_funding_rate(instrument):
    """Get funding rate via REST API"""
    try:
        client = get_info_client()
        now = int(time.time() * 1000)
        funding_history = client.funding_history(instrument, now - 2 * 60 * 60 * 1000, now)
        
        if not funding_history:
            return None
        
        latest_funding = funding_history[0]
        funding_time = latest_funding['time'] / 1000
        hours_to_next = (8 - ((datetime.fromtimestamp(funding_time).hour) % 8)) % 8
        next_funding_time = funding_time + hours_to_next * 3600
        
        return {
            'instrument': instrument,
            'rate': float(latest_funding['fundingRate']),
            'premium': float(latest_funding.get('premium', 0)),
            'next_funding_time': next_funding_time,
            'timestamp': latest_funding['time']
        }
    except Exception as e:
        st.error(f"Error fetching funding rate for {instrument}: {str(e)}")
        return None

# UI Layout
st.title("🚀 Crypto Arbitrage Dashboard")
st.markdown("Real-time monitoring of order books, funding rates, and large trades")

# Sidebar
st.sidebar.title("Dashboard Controls")

# Instrument selection
instruments = st.sidebar.multiselect(
    "Select Instruments",
    ["BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE", "UNI", "MATIC"],
    default=["BTC", "ETH", "SOL"]
)
st.session_state.selected_instruments = instruments

# Trade threshold
trade_threshold = st.sidebar.slider(
    "Trade Threshold ($)",
    min_value=100,
    max_value=50000,
    value=1000,
    step=100,
    help="Show trades larger than this amount"
)
st.session_state.trade_threshold = trade_threshold

# WebSocket controls
st.sidebar.subheader("WebSocket Connection")

if st.session_state.ws_state.connected:
    st.sidebar.success("✅ Connected")
    st.sidebar.write(f"Messages: {st.session_state.ws_state.message_count}")
    st.sidebar.write(f"Queue: {st.session_state.message_queue.qsize()}")
    
    if st.sidebar.button("Disconnect"):
        if st.session_state.ws_state.ws_instance:
            st.session_state.ws_state.ws_instance.close()
        st.session_state.ws_state.connected = False
        st.rerun()
else:
    st.sidebar.error("❌ Disconnected")
    if st.sidebar.button("Connect WebSocket"):
        result = start_websocket_thread()
        st.sidebar.info(result)
        time.sleep(1)
        st.rerun()

# Process messages
if st.session_state.ws_state.connected:
    processed_count, trade_count = process_websocket_messages()
    if processed_count > 0:
        st.sidebar.text(f"Processed: {processed_count} msgs")
        st.sidebar.text(f"Trades: {trade_count}")

# Main tabs
tab1, tab2, tab3, tab4 = st.tabs(["📊 Live Trades", "📈 Order Books", "💰 Funding Rates", "🔧 Debug"])

# Tab 1: Live Trades
with tab1:
    st.header("Live Large Trades")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Total Trades", len(st.session_state.trades))
    
    with col2:
        st.metric("Trade Threshold", f"${trade_threshold:,}")
    
    with col3:
        if st.session_state.trades:
            latest_trade = st.session_state.trades[-1]
            st.metric("Latest Trade", f"{latest_trade['instrument']} ${latest_trade['notional']:,.0f}")
    
    if st.session_state.trades:
        # Create DataFrame
        trades_df = pd.DataFrame(st.session_state.trades)
        trades_df = trades_df.sort_values('time', ascending=False)
        
        # Display recent trades table
        st.subheader("Recent Large Trades")
        
        display_df = trades_df.head(50).copy()
        display_df['time_str'] = display_df['time'].dt.strftime('%H:%M:%S')
        display_df['notional_str'] = display_df['notional'].apply(lambda x: f"${x:,.0f}")
        display_df['price_str'] = display_df['price'].apply(lambda x: f"${x:,.2f}")
        display_df['size_str'] = display_df['size'].apply(lambda x: f"{x:.4f}")
        
        # Color code by side
        def color_side(side):
            return "🟢 Buy" if side == "Buy" else "🔴 Sell"
        
        display_df['side_colored'] = display_df['side'].apply(color_side)
        
        st.dataframe(
            display_df[['time_str', 'instrument', 'side_colored', 'price_str', 'size_str', 'notional_str']].rename(columns={
                'time_str': 'Time',
                'instrument': 'Instrument', 
                'side_colored': 'Side',
                'price_str': 'Price',
                'size_str': 'Size',
                'notional_str': 'Notional'
            }),
            use_container_width=True,
            height=400
        )
        
        # Trade volume chart
        if len(trades_df) > 1:
            st.subheader("Trade Volume by Instrument")
            
            volume_by_instrument = trades_df.groupby(['instrument', 'side'])['notional'].sum().reset_index()
            
            fig = go.Figure()
            
            for instrument in instruments:
                instrument_data = volume_by_instrument[volume_by_instrument['instrument'] == instrument]
                
                buy_data = instrument_data[instrument_data['side'] == 'Buy']
                buy_volume = buy_data['notional'].sum() if not buy_data.empty else 0
                
                sell_data = instrument_data[instrument_data['side'] == 'Sell']
                sell_volume = sell_data['notional'].sum() if not sell_data.empty else 0
                
                fig.add_trace(go.Bar(
                    x=[instrument],
                    y=[buy_volume],
                    name=f'{instrument} Buy',
                    marker_color='green',
                    opacity=0.8
                ))
                
                fig.add_trace(go.Bar(
                    x=[instrument],
                    y=[sell_volume],
                    name=f'{instrument} Sell',
                    marker_color='red',
                    opacity=0.8
                ))
            
            fig.update_layout(
                title="Trade Volume by Instrument and Side",
                xaxis_title="Instrument",
                yaxis_title="Volume ($)",
                barmode='group',
                height=400,
                template="plotly_white"
            )
            
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(f"🔍 Waiting for large trades (>${trade_threshold:,})...")
        st.write("Make sure WebSocket is connected to see real-time trades.")

# Tab 2: Order Books
with tab2:
    st.header("Order Books")
    
    if st.button("Refresh Order Books"):
        st.cache_data.clear()
        st.rerun()
    
    # Display order books for each instrument
    for instrument in instruments:
        st.subheader(f"{instrument} Order Book")
        
        # Try to get from WebSocket first, then REST API
        order_book = st.session_state.order_books.get(instrument)
        if not order_book:
            order_book = get_order_book(instrument)
        
        if order_book and 'levels' in order_book and len(order_book['levels']) >= 2:
            bids = order_book['levels'][0][:10]
            asks = order_book['levels'][1][:10]
            
            # Create dataframes
            bid_df = pd.DataFrame(bids)
            bid_df['px'] = pd.to_numeric(bid_df['px'])
            bid_df['sz'] = pd.to_numeric(bid_df['sz'])
            bid_df = bid_df.sort_values('px', ascending=False)
            
            ask_df = pd.DataFrame(asks)
            ask_df['px'] = pd.to_numeric(ask_df['px'])
            ask_df['sz'] = pd.to_numeric(ask_df['sz'])
            ask_df = ask_df.sort_values('px', ascending=True)
            
            # Calculate metrics
            top_bid = bid_df.iloc[0]['px']
            top_ask = ask_df.iloc[0]['px']
            mid_price = (top_bid + top_ask) / 2
            spread = top_ask - top_bid
            spread_bps = (spread / mid_price) * 10000
            
            # Display metrics
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Top Bid", f"${top_bid:.4f}")
            col2.metric("Top Ask", f"${top_ask:.4f}")
            col3.metric("Mid Price", f"${mid_price:.4f}")
            col4.metric("Spread", f"{spread_bps:.2f} bps")
            
            # Display order book tables
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Bids**")
                bid_display = bid_df[['px', 'sz']].copy()
                bid_display.columns = ['Price', 'Size']
                st.dataframe(bid_display.head(5), use_container_width=True)
            
            with col2:
                st.write("**Asks**")
                ask_display = ask_df[['px', 'sz']].copy()
                ask_display.columns = ['Price', 'Size']
                st.dataframe(ask_display.head(5), use_container_width=True)
        else:
            st.warning(f"No order book data available for {instrument}")

# Tab 3: Funding Rates
with tab3:
    st.header("Funding Rates")
    
    if st.button("Refresh Funding Rates"):
        st.cache_data.clear()
        st.rerun()
    
    funding_data = []
    for instrument in instruments:
        funding = get_funding_rate(instrument)
        if funding:
            funding_data.append({
                'Instrument': instrument,
                'Rate (%)': funding['rate'] * 100,
                'Annualized (%)': funding['rate'] * 3 * 365 * 100,
                'Next Funding': datetime.fromtimestamp(funding['next_funding_time']),
                'Time to Next (hrs)': (datetime.fromtimestamp(funding['next_funding_time']) - datetime.now()).total_seconds() / 3600
            })
    
    if funding_data:
        funding_df = pd.DataFrame(funding_data)
        st.dataframe(funding_df, use_container_width=True)
        
        # Visualization
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=funding_df['Instrument'],
            y=funding_df['Annualized (%)'],
            marker_color='blue'
        ))
        
        fig.update_layout(
            title="Annualized Funding Rates",
            xaxis_title="Instrument",
            yaxis_title="Annualized Rate (%)",
            height=400
        )
        
        st.plotly_chart(fig, use_container_width=True)

# Tab 4: Debug
with tab4:
    st.header("Debug Information")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("WebSocket Status")
        st.write(f"Connected: {st.session_state.ws_state.connected}")
        st.write(f"Messages Received: {st.session_state.ws_state.message_count}")
        st.write(f"Queue Size: {st.session_state.message_queue.qsize()}")
        st.write(f"Total Trades: {len(st.session_state.trades)}")
        st.write(f"Order Books: {len(st.session_state.order_books)}")
        
        if st.button("Clear All Data"):
            st.session_state.trades = []
            st.session_state.order_books = {}
            st.session_state.latest_prices = {}
            st.session_state.debug_messages = []
            st.success("Data cleared!")
    
    with col2:
        st.subheader("Latest Prices")
        if st.session_state.latest_prices:
            for coin, price in st.session_state.latest_prices.items():
                st.write(f"{coin}: ${price:,.2f}")
    
    st.subheader("Debug Messages")
    if st.session_state.debug_messages:
        debug_text = '\n'.join(st.session_state.debug_messages[-20:])
        st.code(debug_text)
    else:
        st.info("No debug messages yet")
    
    if st.button("Clear Debug Messages"):
        st.session_state.debug_messages = []
        st.success("Debug messages cleared!")

# Auto-refresh
if st.sidebar.checkbox("Auto-refresh", value=True):
    refresh_rate = st.sidebar.slider("Refresh rate (seconds)", 1, 30, 3)
    time.sleep(refresh_rate)
    st.rerun()
