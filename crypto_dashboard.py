# File: crypto_dashboard.py
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import time
import json
from datetime import datetime, timedelta
import threading
import queue
import sys
import os
import websocket
import traceback
import uuid


# Create global thread-safe queues for communication between threads
ws_message_queue = queue.Queue()
debug_message_queue = queue.Queue()  # New queue for debug messages
ws_connected = False
ws_instance = None  # Global WebSocket instance

# Try to import hyperliquid
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
if 'debug_msgs' not in st.session_state:  # Make sure this is initialized
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

# Helper function to add debug message to the queue instead of directly to session state
def add_debug_msg(msg):
    """Add debug message to the queue for processing in the main thread"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    debug_message_queue.put(f"{timestamp}: {msg}")
    # Print to console as well for debugging
    print(f"{timestamp}: {msg}")

# WebSocket connection functions
# Update the WebSocket connection and handling functions
def on_message(ws, message):
    """Handle incoming WebSocket messages"""
    try:
        data = json.loads(message)
        ws_message_queue.put(data)
        
        # Debug first few messages for troubleshooting
        queue_size = ws_message_queue.qsize()
        if queue_size < 5 or queue_size % 100 == 0:
            add_debug_msg(f"WebSocket message received: Channel={data.get('channel', 'unknown')}, Queue size={queue_size}")
            print(f"WebSocket message: {message[:100]}...")
    except Exception as e:
        print(f"Error in on_message: {str(e)}")
        add_debug_msg(f"Error processing WebSocket message: {str(e)}")

def on_error(ws, error):
    """Handle WebSocket errors"""
    error_str = str(error)
    print(f"WebSocket error: {error_str}")
    add_debug_msg(f"WebSocket error: {error_str}")
    global ws_connected
    ws_connected = False

def on_close(ws, close_status_code, close_msg):
    """Handle WebSocket connection close"""
    msg = f"WebSocket closed: {close_msg} (code: {close_status_code})"
    print(msg)
    add_debug_msg(msg)
    global ws_connected
    ws_connected = False

def on_open(ws):
    """Handle WebSocket connection open"""
    print("WebSocket connection opened successfully")
    add_debug_msg("WebSocket connection opened successfully")
    global ws_connected
    ws_connected = True
    
    # Subscribe to relevant channels for each instrument
    subscription_id = int(time.time() * 1000)  # Use timestamp as subscription ID
    
    for instrument in instruments:
        try:
            # Subscribe to trades with proper format for Hyperliquid
            trade_sub = {
                "method": "subscribe",
                "subscription": {
                    "type": "trades", 
                    "coin": instrument
                }
            }
            
            # Log before sending
            print(f"Subscribing to trades for {instrument}: {json.dumps(trade_sub)}")
            add_debug_msg(f"Subscribing to trades for {instrument}")
            
            # Send subscription request
            ws.send(json.dumps(trade_sub))
            
            # Add a small delay between subscriptions to avoid overwhelming the server
            time.sleep(0.1)
            
            # Subscribe to order book
            book_sub = {
                "method": "subscribe", 
                "subscription": {
                    "type": "l2Book", 
                    "coin": instrument
                }
            }
            
            # Log before sending
            print(f"Subscribing to L2 book for {instrument}: {json.dumps(book_sub)}")
            add_debug_msg(f"Subscribing to L2 book for {instrument}")
            
            # Send subscription request
            ws.send(json.dumps(book_sub))
            
            # Add a small delay between subscriptions
            time.sleep(0.1)
            
            # Also try subscribing to "bbo" if available
            bbo_sub = {
                "method": "subscribe", 
                "subscription": {
                    "type": "bbo", 
                    "coin": instrument
                }
            }
            
            # Log before sending
            print(f"Subscribing to BBO for {instrument}: {json.dumps(bbo_sub)}")
            add_debug_msg(f"Subscribing to BBO for {instrument}")
            
            # Send subscription request
            ws.send(json.dumps(bbo_sub))
            
            # Add a small delay between subscriptions
            time.sleep(0.1)
            
            # Also try subscribing to "allMids" channel
            if instrument == instruments[0]:  # Only need to subscribe once
                allmids_sub = {
                    "method": "subscribe", 
                    "subscription": {
                        "type": "allMids"
                    }
                }
                
                # Log before sending
                print(f"Subscribing to allMids: {json.dumps(allmids_sub)}")
                add_debug_msg(f"Subscribing to allMids")
                
                # Send subscription request
                ws.send(json.dumps(allmids_sub))
            
        except Exception as e:
            error_msg = f"Error subscribing to {instrument}: {str(e)}"
            print(error_msg)
            print(traceback.format_exc())
            add_debug_msg(error_msg)

def run_websocket():
    """WebSocket connection function for background thread"""
    global ws_instance
    ws_url = "wss://api.hyperliquid.xyz/ws"
    
    # Enable tracing for debugging - only during development
    websocket.enableTrace(True)
    
    while True:
        try:
            # Create WebSocket instance with specific options
            ws = websocket.WebSocketApp(ws_url,
                                      on_open=on_open,
                                      on_message=on_message,
                                      on_error=on_error,
                                      on_close=on_close)
            
            # Set global instance so it can be accessed elsewhere
            ws_instance = ws
            
            # Add more debug logging
            print(f"Starting WebSocket connection to {ws_url}")
            add_debug_msg(f"Starting WebSocket connection to {ws_url}")
            
            # Run the WebSocket connection with specific options
            ws.run_forever(ping_interval=30, 
                           ping_timeout=10, 
                           reconnect=5)  # Auto reconnect every 5 seconds
            
            # If we get here, the connection closed
            print("WebSocket connection ended, waiting before reconnecting...")
            add_debug_msg("WebSocket connection ended, waiting before reconnecting...")
            
            # Wait before reconnecting
            time.sleep(5)
        except Exception as e:
            error_msg = f"Error in WebSocket thread: {str(e)}"
            print(error_msg)
            print(traceback.format_exc())
            add_debug_msg(error_msg)
            time.sleep(5)  # Wait before retry

# Improved message processing function
def process_websocket_messages():
    """Process queued WebSocket messages from main thread"""
    processed_count = 0
    trade_count = 0
    orderbook_count = 0
    other_count = 0
    
    # First, process any debug messages that have been queued
    debug_count = 0
    while not debug_message_queue.empty():
        try:
            debug_msg = debug_message_queue.get_nowait()
            debug_count += 1
            # Now it's safe to access session state in the main thread
            st.session_state.debug_msgs.append(debug_msg)
            # Keep the debug message list at a reasonable size
            if len(st.session_state.debug_msgs) > 50:  # Increased from 20 to 50
                st.session_state.debug_msgs = st.session_state.debug_msgs[-50:]
        except queue.Empty:
            break
        except Exception as e:
            print(f"Error processing debug message: {str(e)}")
    
    # Process WebSocket messages
    while not ws_message_queue.empty() and processed_count < 100:
        try:
            message = ws_message_queue.get_nowait()
            processed_count += 1
            
            # Debug message when processing starts
            if processed_count == 1:
                add_debug_msg(f"Processing WebSocket messages from queue (size: {ws_message_queue.qsize()})")
            
            # Process by message channel
            channel = message.get('channel', 'unknown')
            
            # Process order book updates
            if channel == 'l2Book' and 'data' in message:
                orderbook_count += 1
                data = message['data']
                instrument = data.get('coin')
                
                if instrument in instruments:
                    st.session_state.order_books[instrument] = data
                    # Debug log for first orderbook update
                    if orderbook_count == 1:
                        add_debug_msg(f"Updated order book for {instrument}")
            
            # Process trade updates
            elif channel == 'trades' and 'data' in message:
                trades = message['data']
                
                for trade in trades:
                    trade_count += 1
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
            
            # Process other message types - for debugging purposes
            else:
                other_count += 1
                if other_count <= 3:  # Limit logging to avoid spam
                    add_debug_msg(f"Received message on channel: {channel}")
        
        except queue.Empty:
            break
        except Exception as e:
            error_msg = f"Error processing WebSocket message: {str(e)}"
            print(error_msg)
            print(traceback.format_exc())
            add_debug_msg(error_msg)
    
    # Update WebSocket status in session state
    current_status = "Connected" if ws_connected else "Disconnected"
    if st.session_state.ws_status != current_status:
        add_debug_msg(f"WebSocket status changed to: {current_status}")
    st.session_state.ws_status = current_status
    
    # Return processing stats
    return (f"Processed: {processed_count} messages "
            f"({trade_count} trades, {orderbook_count} orderbooks, {other_count} other, {debug_count} debug)"), processed_count

# Improved WebSocket starter
def start_websocket():
    """Start WebSocket connection in a background thread"""
    global ws_connected, ws_instance
    
    # Check if already connected
    if ws_connected and ws_instance:
        add_debug_msg("WebSocket already connected")
        return "WebSocket already connected"
    
    # Close existing connection if it exists
    if ws_instance:
        try:
            ws_instance.close()
            time.sleep(1)  # Wait for closure
        except:
            pass
    
    # Create and start thread
    ws_thread = threading.Thread(target=run_websocket, daemon=True)
    ws_thread.start()
    add_debug_msg(f"WebSocket connection thread started (ID: {ws_thread.ident})")
    return "Starting WebSocket connection..."

# Process queued messages from WebSocket thread
def process_websocket_messages():
    """Process queued WebSocket messages from main thread"""
    processed_count = 0
    
    # First, process any debug messages that have been queued
    while not debug_message_queue.empty():
        try:
            debug_msg = debug_message_queue.get_nowait()
            # Now it's safe to access session state in the main thread
            st.session_state.debug_msgs.append(debug_msg)
            # Keep the debug message list at a reasonable size
            if len(st.session_state.debug_msgs) > 20:
                st.session_state.debug_msgs = st.session_state.debug_msgs[-20:]
        except queue.Empty:
            break
    
    # Process up to 100 messages at a time to avoid blocking
    while not ws_message_queue.empty() and processed_count < 100:
        try:
            message = ws_message_queue.get_nowait()
            processed_count += 1
            
            # Debug first few messages
            if processed_count < 5:
                # Add to queue instead of directly to session state
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

# Rest of the code remains the same...

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
# In your Recent Trades Tab (tab3), update the WebSocket status display
with tab3:
    st.header("Recent Large Trades")
    
    # WebSocket status and controls with more detailed information
    col1, col2 = st.columns([3, 1])
    
    with col1:
        if st.session_state.ws_status == "Connected":
            st.success("WebSocket Connected - Receiving Live Trade Data")
            st.info(f"Queue size: {ws_message_queue.qsize()} | Recorded trades: {len(st.session_state.trades)}")
        else:
            st.warning("WebSocket Disconnected - Click 'Connect WebSocket' to start")
            
            # Add troubleshooting info
            if len(st.session_state.debug_msgs) > 0:
                last_debug = st.session_state.debug_msgs[-1]
                st.error(f"Last debug message: {last_debug}")
    
    with col2:
        if st.button("Connect WebSocket", key="trades_connect"):
            result = start_websocket()
            st.info(result)
            
            # Automatically rerun after a delay to show updated status
            time.sleep(3)
            st.rerun()
    
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
# Add this to your Debug tab section
with tab4:
    st.header("Debug Information")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("WebSocket Status")
        st.write(f"Connected: {ws_connected}")
        st.write(f"Message Queue Size: {ws_message_queue.qsize()}")
        st.write(f"Debug Queue Size: {debug_message_queue.qsize()}")
        
        if st.button("Force Reconnect", key="debug_reconnect"):
            # Close existing connection if it exists
            if ws_instance:
                try:
                    ws_instance.close()
                except:
                    pass
            # Start a new connection
            add_debug_msg("Manual reconnect initiated")
            ws_connected = False
            start_websocket()
            st.success("Reconnecting...")
        
        # Add a manual ping button
        if st.button("Send Ping", key="send_ping"):
            if ws_instance and ws_connected:
                try:
                    ws_instance.send(json.dumps({"method": "ping"}))
                    add_debug_msg("Manual ping sent")
                    st.success("Ping sent!")
                except Exception as e:
                    add_debug_msg(f"Error sending ping: {str(e)}")
                    st.error(f"Error: {str(e)}")
            else:
                st.error("WebSocket not connected")
        
        # Add a test subscription button
        if st.button("Test BTC Subscription", key="test_sub"):
            if ws_instance and ws_connected:
                try:
                    # Try a simple test subscription
                    test_sub = {
                        "method": "subscribe",
                        "subscription": {
                            "type": "trades", 
                            "coin": "BTC"
                        }
                    }
                    ws_instance.send(json.dumps(test_sub))
                    add_debug_msg("Test BTC trades subscription sent")
                    st.success("Test subscription sent!")
                except Exception as e:
                    add_debug_msg(f"Error sending test subscription: {str(e)}")
                    st.error(f"Error: {str(e)}")
            else:
                st.error("WebSocket not connected")
    
    with col2:
        st.subheader("Debug Messages")
        if st.button("Refresh Debug Log", key="refresh_debug"):
            st.rerun()
        st.code('\n'.join(st.session_state.debug_msgs))
        
        if st.button("Clear Debug Messages", key="clear_debug"):
            st.session_state.debug_msgs = []
    
    # Show raw WebSocket data
    st.subheader("Raw Trade Data")
    if st.session_state.trades:
        st.write(f"Total trades recorded: {len(st.session_state.trades)}")
        st.json(st.session_state.trades[-5:])  # Show last 5 trades
    else:
        st.info("No trade data available yet")
        
# At the bottom of your script, update the processing call
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
    
    # Add WebSocket message processing stats to sidebar
    if ws_connected:
        st.sidebar.success(f"WebSocket connected | Queue: {ws_message_queue.qsize()}")
    else:
        st.sidebar.error("WebSocket disconnected")
    
    # Sleep and rerun
    time.sleep(refresh_rate)
    st.rerun()