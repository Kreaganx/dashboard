# File: crypto_dashboard.py
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import time
import json
from datetime import datetime
import threading
import queue
import sys
import os
import websocket

# Create a global thread-safe queue for WebSocket messages
# This is accessible from any thread
ws_message_queue = queue.Queue()
ws_connected = False
ws_instance = None  # Global WebSocket instance

# Add hyperliquid imports
try:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from hyperliquid.info import Info
except ImportError:
    st.error("Could not import Hyperliquid. Make sure the path is correct.")

# Set page configuration
st.set_page_config(
    page_title="Crypto Arbitrage Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize session state
if 'order_books' not in st.session_state:
    st.session_state.order_books = {}
if 'funding_rates' not in st.session_state:
    st.session_state.funding_rates = {}
if 'trades' not in st.session_state:
    st.session_state.trades = []
if 'last_update' not in st.session_state:
    st.session_state.last_update = datetime.now()
if 'ws_status' not in st.session_state:
    st.session_state.ws_status = "Disconnected"
if 'debug_msgs' not in st.session_state:
    st.session_state.debug_msgs = []

# Sidebar configuration
st.sidebar.title("Arbitrage Dashboard Controls")
instruments = st.sidebar.multiselect(
    "Select Instruments",
    ["BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE"],
    default=["BTC", "ETH", "SOL"]
)

update_interval = st.sidebar.slider(
    "Update Interval (seconds)",
    min_value=5,
    max_value=60,
    value=15,
    step=5
)

# Helper function to add debug message
def add_debug_msg(msg):
    """Add debug message to session state"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.debug_msgs.append(f"{timestamp}: {msg}")
    if len(st.session_state.debug_msgs) > 20:
        st.session_state.debug_msgs = st.session_state.debug_msgs[-20:]

# WebSocket connection functions
def on_message(ws, message):
    """Handle incoming WebSocket messages"""
    try:
        data = json.loads(message)
        ws_message_queue.put(data)
        # Print out first few messages for debugging
        if ws_message_queue.qsize() < 10:
            print(f"WebSocket message received: {message[:100]}...")
    except Exception as e:
        print(f"Error in on_message: {str(e)}")

def on_error(ws, error):
    """Handle WebSocket errors"""
    print(f"WebSocket error: {error}")
    add_debug_msg(f"WebSocket error: {error}")
    global ws_connected
    ws_connected = False

def on_close(ws, close_status_code, close_msg):
    """Handle WebSocket connection close"""
    print(f"WebSocket closed: {close_msg} ({close_status_code})")
    add_debug_msg(f"WebSocket closed: {close_msg} ({close_status_code})")
    global ws_connected
    ws_connected = False

def on_open(ws):
    """Handle WebSocket connection open"""
    print("WebSocket connection opened")
    add_debug_msg("WebSocket connection opened")
    global ws_connected
    ws_connected = True
    
    # Subscribe to relevant channels for each instrument
    for instrument in instruments:
        try:
            # Subscribe to trades
            trade_sub = {
                "method": "subscribe",
                "subscription": {"type": "trades", "coin": instrument}
            }
            ws.send(json.dumps(trade_sub))
            print(f"Subscribed to trades for {instrument}")
            add_debug_msg(f"Subscribed to trades for {instrument}")
            
            # Subscribe to order book
            book_sub = {
                "method": "subscribe", 
                "subscription": {"type": "l2Book", "coin": instrument}
            }
            ws.send(json.dumps(book_sub))
            print(f"Subscribed to L2 book for {instrument}")
            add_debug_msg(f"Subscribed to L2 book for {instrument}")
        except Exception as e:
            print(f"Error subscribing to {instrument}: {e}")
            add_debug_msg(f"Error subscribing to {instrument}: {e}")

def run_websocket():
    """WebSocket connection function for background thread"""
    global ws_instance
    ws_url = "wss://api.hyperliquid.xyz/ws"
    
    # Enable tracing for debugging
    websocket.enableTrace(True)
    
    # Create WebSocket instance
    ws = websocket.WebSocketApp(ws_url,
                              on_open=on_open,
                              on_message=on_message,
                              on_error=on_error,
                              on_close=on_close)
    
    ws_instance = ws
    ws.run_forever()

# Start WebSocket in background thread
def start_websocket():
    """Start WebSocket connection in a background thread"""
    global ws_connected
    
    # Check if already connected
    if ws_connected:
        return "WebSocket already connected"
    
    # Create and start thread
    ws_thread = threading.Thread(target=run_websocket, daemon=True)
    ws_thread.start()
    add_debug_msg("WebSocket thread started")
    return "Starting WebSocket connection..."

def process_websocket_messages():
    """Process queued WebSocket messages from main thread"""
    processed_count = 0
    
    # Process up to 100 messages at a time to avoid blocking
    while not ws_message_queue.empty() and processed_count < 100:
        try:
            message = ws_message_queue.get_nowait()
            processed_count += 1
            
            # Debug first few messages
            if processed_count < 5:
                add_debug_msg(f"Processing message: {message.get('channel', 'unknown')} channel")
            
            # Process order book updates
            if message.get('channel') == 'l2Book' and 'data' in message:
                data = message['data']
                instrument = data.get('coin')
                
                if instrument in instruments:
                    st.session_state.order_books[instrument] = data
            
            # Process trade updates
            elif message.get('channel') == 'trades' and 'data' in message:
                trades = message['data']
                
                for trade in trades:
                    instrument = trade.get('coin')
                    
                    if instrument in instruments:
                        # Convert A/B to Sell/Buy
                        side_code = trade.get('side')
                        side = "Sell" if side_code == "A" else "Buy" if side_code == "B" else side_code
                        
                        # If size or price is missing, skip this trade
                        if 'sz' not in trade or 'px' not in trade:
                            continue
                            
                        size = float(trade.get('sz', 0))
                        price = float(trade.get('px', 0))
                        notional = size * price
                        
                        # Record large trades (over $50,000)
                        if notional > 50000:
                            trade_time = datetime.fromtimestamp(int(trade.get('time', time.time() * 1000)) / 1000)
                            
                            add_debug_msg(f"Large trade: {instrument} {side} {size} @ {price} (${notional:.2f})")
                            
                            st.session_state.trades.append({
                                'instrument': instrument,
                                'side': side,
                                'size': size,
                                'price': price,
                                'notional': notional,
                                'time': trade_time
                            })
                            
                            # Keep only the most recent 100 trades
                            if len(st.session_state.trades) > 100:
                                st.session_state.trades = st.session_state.trades[-100:]
        
        except queue.Empty:
            break
        except Exception as e:
            add_debug_msg(f"Error processing message: {str(e)}")
    
    # Update WebSocket status in session state
    st.session_state.ws_status = "Connected" if ws_connected else "Disconnected"
    
    # Return some stats
    return f"Processed {processed_count} messages", processed_count

# Main data fetching functions
@st.cache_data(ttl=update_interval)
def get_order_book(instrument):
    info_client = Info(skip_ws=True)
    try:
        return info_client.l2_snapshot(instrument)
    except Exception as e:
        st.error(f"Error fetching order book for {instrument}: {str(e)}")
        return None

@st.cache_data(ttl=update_interval*2)  # Funding rates change less frequently
def get_funding_rate(instrument):
    info_client = Info(skip_ws=True)
    try:
        now = int(time.time() * 1000)
        # Get funding history for the last 2 hours
        funding_history = info_client.funding_history(instrument, now - 2 * 60 * 60 * 1000, now)
        
        if not funding_history:
            return None
        
        # Get the most recent entry
        latest_funding = funding_history[0]
        
        # Calculate next funding time (funding is every 8 hours at fixed times)
        funding_time = latest_funding['time'] / 1000  # Convert to seconds
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

# Layout
st.title("Crypto Market Data Dashboard")
st.markdown("Real-time monitoring of order books and funding rates")

# WebSocket connection status indicator in sidebar
if st.session_state.ws_status == "Connected":
    st.sidebar.success("WebSocket Connected")
else:
    st.sidebar.error("WebSocket Disconnected")
    if st.sidebar.button("Connect WebSocket", key="sidebar_connect"):
        start_websocket()
        st.sidebar.info("Connecting...")

# Create tabs
tab1, tab2, tab3, tab4 = st.tabs(["Order Books", "Funding Rates", "Recent Trades", "Debug"])

# Update data on interval
def update_data():
    for instrument in instruments:
        order_book = get_order_book(instrument)
        if order_book:
            st.session_state.order_books[instrument] = order_book
        
        funding_rate = get_funding_rate(instrument)
        if funding_rate:
            st.session_state.funding_rates[instrument] = funding_rate
    
    st.session_state.last_update = datetime.now()

# Order Book Tab
with tab1:
    st.header("Order Book Data")
    
    # Update button
    if st.button("Refresh Order Books", key="refresh_books"):
        update_data()
    
    # Last update time
    st.text(f"Last updated: {st.session_state.last_update.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Create columns for multiple instruments
    cols = st.columns(len(instruments))
    
    for i, instrument in enumerate(instruments):
        with cols[i]:
            st.subheader(f"{instrument} Order Book")
            
            if instrument in st.session_state.order_books:
                order_book = st.session_state.order_books[instrument]
                
                if 'levels' in order_book and len(order_book['levels']) >= 2:
                    bids = order_book['levels'][0][:5]  # Top 5 bids
                    asks = order_book['levels'][1][:5]  # Top 5 asks
                    
                    # Create bid dataframe
                    bid_df = pd.DataFrame(bids)
                    bid_df['px'] = pd.to_numeric(bid_df['px'])
                    bid_df['sz'] = pd.to_numeric(bid_df['sz'])
                    bid_df['total'] = bid_df['px'] * bid_df['sz']
                    bid_df.columns = ['Price', 'Size', 'Orders', 'Value']
                    
                    # Create ask dataframe
                    ask_df = pd.DataFrame(asks)
                    ask_df['px'] = pd.to_numeric(ask_df['px'])
                    ask_df['sz'] = pd.to_numeric(ask_df['sz'])
                    ask_df['total'] = ask_df['px'] * ask_df['sz']
                    ask_df.columns = ['Price', 'Size', 'Orders', 'Value']
                    
                    # Calculate mid price and spread
                    top_bid = float(bids[0]['px'])
                    top_ask = float(asks[0]['px'])
                    mid_price = (top_bid + top_ask) / 2
                    spread = top_ask - top_bid
                    spread_bps = (spread / mid_price) * 10000
                    
                    # Display metrics
                    col1, col2 = st.columns(2)
                    col1.metric("Mid Price", f"${mid_price:.2f}")
                    col2.metric("Spread", f"{spread_bps:.2f} bps")
                    
                    # Display order book tables
                    st.markdown("**Asks**")
                    st.dataframe(ask_df[['Price', 'Size', 'Value']].sort_values('Price', ascending=True), use_container_width=True)
                    
                    st.markdown("**Bids**")
                    st.dataframe(bid_df[['Price', 'Size', 'Value']].sort_values('Price', ascending=False), use_container_width=True)
                    
                    # Create order book visualization
                    fig = go.Figure()
                    
                    # Add asks
                    fig.add_trace(go.Bar(
                        x=ask_df['Price'],
                        y=ask_df['Size'],
                        name='Asks',
                        marker_color='red',
                        orientation='v'
                    ))
                    
                    # Add bids
                    fig.add_trace(go.Bar(
                        x=bid_df['Price'],
                        y=bid_df['Size'],
                        name='Bids',
                        marker_color='green',
                        orientation='v'
                    ))
                    
                    fig.update_layout(
                        title=f"{instrument} Order Book Depth",
                        xaxis_title="Price",
                        yaxis_title="Size",
                        barmode='group',
                        height=300,
                        margin=dict(l=0, r=0, t=40, b=0),
                    )
                    
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning(f"No valid order book data for {instrument}")
            else:
                st.info(f"Loading order book data for {instrument}...")

# Funding Rates Tab
with tab2:
    st.header("Funding Rate Data")
    
    # Update button
    if st.button("Refresh Funding Rates", key="refresh_funding"):
        update_data()
    
    # Funding rate metrics
    funding_data = []
    for instrument in instruments:
        if instrument in st.session_state.funding_rates:
            funding = st.session_state.funding_rates[instrument]
            funding_data.append({
                'Instrument': instrument,
                'Rate': funding['rate'] * 100,  # Convert to percentage
                'Annualized': funding['rate'] * 3 * 365 * 100,  # 3 funding events per day
                'Next Funding': datetime.fromtimestamp(funding['next_funding_time']),
                'Time to Next': (datetime.fromtimestamp(funding['next_funding_time']) - datetime.now()).total_seconds() / 3600  # hours
            })
    
    if funding_data:
        # Create dataframe
        funding_df = pd.DataFrame(funding_data)
        
        # Display metrics
        st.subheader("Current Funding Rates")
        metrics_cols = st.columns(len(funding_data))
        
        for i, data in enumerate(funding_data):
            with metrics_cols[i]:
                st.metric(
                    data['Instrument'], 
                    f"{data['Rate']:.6f}%",
                    f"{data['Annualized']:.2f}% ann."
                )
                st.text(f"Next: {data['Next Funding'].strftime('%H:%M:%S')}")
                st.text(f"In: {data['Time to Next']:.1f} hours")
        
        # Create visualization
        st.subheader("Funding Rate Comparison")
        fig = go.Figure()
        
        # Add bars for current rates
        fig.add_trace(go.Bar(
            x=[data['Instrument'] for data in funding_data],
            y=[data['Annualized'] for data in funding_data],
            name='Annualized Funding Rate (%)',
            marker_color='blue'
        ))
        
        fig.update_layout(
            title="Annualized Funding Rates by Instrument",
            xaxis_title="Instrument",
            yaxis_title="Annualized Rate (%)",
            height=400,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Display raw data
        st.subheader("Funding Rate Details")
        st.dataframe(funding_df, use_container_width=True)
    else:
        st.info("Loading funding rate data...")

# Recent Trades Tab
with tab3:
    st.header("Recent Large Trades")
    
    # WebSocket status and controls
    col1, col2 = st.columns([3, 1])
    
    with col1:
        if st.session_state.ws_status == "Connected":
            st.success("WebSocket Connected - Receiving Live Trade Data")
        else:
            st.warning("WebSocket Disconnected - Click 'Connect WebSocket' to start")
    
    with col2:
        if st.button("Connect WebSocket", key="trades_connect"):
            start_websocket()
            st.info("Connecting...")
    
    # Display trade count
    if st.session_state.trades:
        st.info(f"Received {len(st.session_state.trades)} large trades (>$50,000)")
    
    # Display recent trades
    if st.session_state.trades:
        # Convert to DataFrame
        trades_df = pd.DataFrame(st.session_state.trades)
        trades_df = trades_df.sort_values('time', ascending=False)
        
        # Format the DataFrame for display
        trades_display = trades_df.copy()
        trades_display['time'] = trades_display['time'].dt.strftime('%H:%M:%S')
        trades_display['notional'] = trades_display['notional'].apply(lambda x: f"${x:,.2f}")
        trades_display['color'] = trades_display['side'].apply(lambda x: "🟢" if x == "Buy" else "🔴")
        trades_display['trade'] = trades_display['color'] + " " + trades_display['side']
        
        # Show recent trades
        st.subheader("Last 100 Large Trades (>$50,000)")
        st.dataframe(
            trades_display[['time', 'instrument', 'trade', 'price', 'size', 'notional']],
            use_container_width=True
        )
        
        # Only show visualizations if we have enough data
        if len(trades_df) > 3:
            # Trade volume visualization
            st.subheader("Trade Volume by Instrument and Side")
            
            # Group by instrument and side
            trade_summary = trades_df.groupby(['instrument', 'side'])['notional'].sum().reset_index()
            
            # Create visualization
            fig = go.Figure()
            
            for instrument in instruments:
                instrument_data = trade_summary[trade_summary['instrument'] == instrument]
                
                buy_data = instrument_data[instrument_data['side'] == 'Buy']
                buy_volume = buy_data['notional'].sum() if not buy_data.empty else 0
                
                sell_data = instrument_data[instrument_data['side'] == 'Sell']
                sell_volume = sell_data['notional'].sum() if not sell_data.empty else 0
                
                fig.add_trace(go.Bar(
                    x=[instrument],
                    y=[buy_volume],
                    name=f'{instrument} Buy',
                    marker_color='green'
                ))
                
                fig.add_trace(go.Bar(
                    x=[instrument],
                    y=[sell_volume],
                    name=f'{instrument} Sell',
                    marker_color='red'
                ))
            
            fig.update_layout(
                title="Large Trade Volume by Instrument and Side",
                xaxis_title="Instrument",
                yaxis_title="Volume ($)",
                barmode='group',
                height=400
            )
            
            st.plotly_chart(fig, use_container_width=True)
            
            # Trade count by instrument
            trade_counts = trades_df['instrument'].value_counts().reset_index()
            trade_counts.columns = ['Instrument', 'Count']
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("Trade Count by Instrument")
                fig = go.Figure(data=[go.Pie(
                    labels=trade_counts['Instrument'],
                    values=trade_counts['Count'],
                    hole=.3
                )])
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                st.subheader("Buy vs Sell Ratio")
                buy_sell_counts = trades_df['side'].value_counts().reset_index()
                buy_sell_counts.columns = ['Side', 'Count']
                
                fig = go.Figure(data=[go.Pie(
                    labels=buy_sell_counts['Side'],
                    values=buy_sell_counts['Count'],
                    hole=.3,
                    marker_colors=['green', 'red']
                )])
                st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Waiting for trades data... Connect WebSocket to see real-time trades.")

# Debug Tab
with tab4:
    st.header("Debug Information")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("WebSocket Status")
        st.write(f"Connected: {ws_connected}")
        st.write(f"Message Queue Size: {ws_message_queue.qsize()}")
        
        if st.button("Force Reconnect", key="debug_reconnect"):
            # Close existing connection if it exists
            if ws_instance:
                ws_instance.close()
            # Start a new connection
            start_websocket()
            st.success("Reconnecting...")
    
    with col2:
        st.subheader("Debug Messages")
        st.code('\n'.join(st.session_state.debug_msgs))
        
        if st.button("Clear Debug Messages", key="clear_debug"):
            st.session_state.debug_msgs = []
    
    # Show raw trade data
    st.subheader("Raw Trade Data")
    if st.session_state.trades:
        st.json(st.session_state.trades[-5:])  # Show last 5 trades
    else:
        st.info("No trade data available yet")

# Process WebSocket messages on each refresh
result, count = process_websocket_messages()
st.sidebar.text(result)

# Start WebSocket connection on initial load if not already started
if st.session_state.ws_status == "Disconnected":
    start_websocket()

# Initial data load if needed
if not st.session_state.order_books or not st.session_state.funding_rates:
    update_data()

# Set up automatic refresh
if st.sidebar.checkbox("Auto-refresh", value=True, key="auto_refresh"):
    refresh_rate = st.sidebar.slider("Refresh rate (sec)", 1, 30, 5, key="refresh_rate")
    st.sidebar.write(f"Dashboard will refresh every {refresh_rate} seconds")
    time.sleep(refresh_rate)
    st.rerun()