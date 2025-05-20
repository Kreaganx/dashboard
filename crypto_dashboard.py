import asyncio
import json
import logging
import time
import hmac
import hashlib
import base64
from typing import Dict, List, Any, Optional, Callable
import aiohttp
import websockets
from datetime import datetime, timedelta
import threading
import queue
import sys
import os
import websocket
import traceback
import uuid
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go

# Create thread-safe queues
ws_message_queue = queue.Queue()
debug_message_queue = queue.Queue()

# Thread-safe WebSocket state manager
class WebSocketState:
    """
    Thread-safe WebSocket state manager to avoid state inconsistencies
    between different parts of the application.
    """
    def __init__(self):
        self.connected = False
        self.last_message_time = 0
        self.thread = None
        self.instance = None
        self.session_id = str(uuid.uuid4())[:8]
        self.reconnect_attempt = 0
        self.lock = threading.Lock()  # For thread safety
    
    def mark_connected(self):
        """Mark WebSocket as connected"""
        with self.lock:
            self.connected = True
            return self.connected
    
    def mark_disconnected(self):
        """Mark WebSocket as disconnected"""
        with self.lock:
            self.connected = False
            return self.connected
    
    def update_last_message_time(self):
        """Update the timestamp of the last received message"""
        with self.lock:
            self.last_message_time = time.time()
            return self.last_message_time
    
    def set_thread(self, thread):
        """Set the current WebSocket thread"""
        with self.lock:
            self.thread = thread
            return self.thread
    
    def set_instance(self, instance):
        """Set the current WebSocket instance"""
        with self.lock:
            self.instance = instance
            return self.instance
    
    def new_session_id(self):
        """Generate a new session ID for a fresh connection"""
        with self.lock:
            self.session_id = str(uuid.uuid4())[:8]
            self.reconnect_attempt += 1
            return self.session_id
    
    def get_status(self):
        """Get the full connection status"""
        with self.lock:
            thread_alive = self.thread is not None and self.thread.is_alive()
            time_since_message = time.time() - self.last_message_time if self.last_message_time > 0 else float('inf')
            
            return {
                'connected': self.connected,
                'thread_alive': thread_alive,
                'last_message_time': self.last_message_time,
                'time_since_message': time_since_message,
                'session_id': self.session_id,
                'reconnect_attempt': self.reconnect_attempt
            }

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

def initialize_dashboard_state():
    """
    Initialize all dashboard state variables with proper defaults
    """
    # Create the WebSocket state manager
    if 'ws_state' not in st.session_state:
        st.session_state.ws_state = WebSocketState()
    
    # Initialize data containers if needed
    if 'order_books' not in st.session_state:
        st.session_state.order_books = {}
    if 'funding_rates' not in st.session_state:
        st.session_state.funding_rates = {}
    if 'trades' not in st.session_state:
        st.session_state.trades = []
    if 'latest_prices' not in st.session_state:
        st.session_state.latest_prices = {}
    if 'last_update' not in st.session_state:
        st.session_state.last_update = datetime.now()
    if 'ws_status' not in st.session_state:
        st.session_state.ws_status = "Disconnected"
    if 'debug_msgs' not in st.session_state:
        st.session_state.debug_msgs = []
    if 'processed_stats' not in st.session_state:
        st.session_state.processed_stats = []
    if 'last_heartbeat' not in st.session_state:
        st.session_state.last_heartbeat = datetime.now()

# Initialize dashboard state
initialize_dashboard_state()

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

# Helper function to add debug message to the queue
def add_debug_msg(msg):
    """
    Add debug message to queue with improved queue management
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    full_msg = f"{timestamp}: {msg}"
    
    # Queue the message
    try:
        debug_message_queue.put(full_msg, block=False)
    except queue.Full:
        # If queue is full, remove oldest messages
        try:
            for _ in range(10):  # Clear 10 messages
                debug_message_queue.get_nowait()
            debug_message_queue.put(full_msg, block=False)
        except:
            pass  # If still fails, just drop this message
    
    # Also print to console for debugging
    print(full_msg)

# WebSocket connection functions
def on_message(ws, message):
    """
    Handle incoming WebSocket messages with improved error handling
    and state management.
    """
    try:
        # Update last message time immediately
        if 'ws_state' in st.session_state:
            st.session_state.ws_state.update_last_message_time()
        
        # Parse and queue the message
        data = json.loads(message)
        ws_message_queue.put(data)
        
        # Auto-mark as connected if receiving messages
        if 'ws_state' in st.session_state and not st.session_state.ws_state.connected:
            debug_message_queue.put("ws_connected:message_received")
            st.session_state.ws_state.mark_connected()
        
        # Track statistics at reduced frequency
        queue_size = ws_message_queue.qsize()
        if queue_size < 5 or queue_size % 100 == 0:
            channel = data.get('channel', 'unknown')
            add_debug_msg(f"WS message: Channel={channel}, Queue size={queue_size}")
            
    except json.JSONDecodeError:
        add_debug_msg(f"Received non-JSON message: {message[:100]}...")
    except Exception as e:
        error_msg = f"Error in on_message: {str(e)}"
        add_debug_msg(error_msg)
        print(traceback.format_exc())

def on_error(ws, error):
    """
    Handle WebSocket errors with improved logging and recovery
    """
    error_str = str(error)
    add_debug_msg(f"WebSocket error: {error_str}")
    print(f"WebSocket error: {error_str}")
    print(traceback.format_exc())
    
    # Mark as disconnected, but don't immediately reconnect
    # This allows the main thread to handle reconnection with backoff
    debug_message_queue.put("ws_disconnected:error")

def on_close(ws, close_status_code, close_msg):
    """
    Handle WebSocket connection close with better state management
    """
    msg = f"WebSocket closed: {close_msg} (code: {close_status_code})"
    add_debug_msg(msg)
    print(msg)
    
    # Mark as disconnected
    debug_message_queue.put("ws_disconnected:closed")

def on_open(ws):
    """
    Handle WebSocket connection open with reliable subscription handling
    and state management
    """
    add_debug_msg("WebSocket connection opened successfully")
    print("WebSocket connection opened successfully")
    
    # Mark as connected immediately
    debug_message_queue.put("ws_connected:open")
    
    # Store thread-local connection state
    current_thread = threading.current_thread()
    current_thread.connection_open = True
    
    # Subscribe to channels with retries and backoff
    subscribe_to_channels(ws, instruments)
    
    # Send initial ping to verify connection
    try:
        ws.send(json.dumps({"method": "ping"}))
        add_debug_msg("Initial ping sent")
    except Exception as e:
        add_debug_msg(f"Error sending initial ping: {str(e)}")

def subscribe_to_channels(ws, instruments, max_retries=3):
    """
    Subscribe to all channels with retry logic and backoff
    """
    successful_subs = 0
    failed_subs = 0
    
    # First subscribe to the allMids channel
    try:
        allmids_sub = {
            "method": "subscribe", 
            "subscription": {
                "type": "allMids"
            }
        }
        ws.send(json.dumps(allmids_sub))
        successful_subs += 1
        time.sleep(0.1)  # Small delay between subscriptions
    except Exception as e:
        add_debug_msg(f"Error subscribing to allMids: {str(e)}")
        failed_subs += 1
    
    # Then subscribe to instrument-specific channels
    for instrument in instruments:
        # Subscribe to trades
        if not subscribe_with_retry(ws, {
            "method": "subscribe",
            "subscription": {"type": "trades", "coin": instrument}
        }, max_retries):
            failed_subs += 1
        else:
            successful_subs += 1
            
        time.sleep(0.1)  # Small delay between subscriptions
        
        # Subscribe to order book
        if not subscribe_with_retry(ws, {
            "method": "subscribe", 
            "subscription": {"type": "l2Book", "coin": instrument}
        }, max_retries):
            failed_subs += 1
        else:
            successful_subs += 1
            
        time.sleep(0.1)  # Small delay between subscriptions
    
    # Log subscription summary
    add_debug_msg(f"Subscriptions: {successful_subs} successful, {failed_subs} failed")

def subscribe_with_retry(ws, subscription, max_retries=3):
    """
    Attempt to subscribe with retries and exponential backoff
    
    Args:
        ws: WebSocket instance
        subscription: Subscription message to send
        max_retries: Maximum number of retry attempts
    
    Returns:
        bool: True if successful, False otherwise
    """
    retry_delay = 0.5  # Initial retry delay in seconds
    
    for attempt in range(max_retries + 1):
        try:
            ws.send(json.dumps(subscription))
            
            if attempt > 0:
                add_debug_msg(f"Subscription successful on attempt {attempt+1}")
            
            return True
        except Exception as e:
            if attempt < max_retries:
                add_debug_msg(f"Subscription attempt {attempt+1} failed: {str(e)}, retrying in {retry_delay}s")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                add_debug_msg(f"Subscription failed after {max_retries+1} attempts: {str(e)}")
    
    return False

def run_websocket(session_id):
    """
    WebSocket connection function with improved error handling,
    reconnection logic, and state management
    """
    ws_url = "wss://api.hyperliquid.xyz/ws"
    thread_id = threading.get_ident()
    current_thread = threading.current_thread()
    
    # Store session ID in thread local storage
    current_thread.session_id = session_id
    current_thread.connection_open = False
    
    add_debug_msg(f"Starting WebSocket session {session_id} in thread {thread_id}")
    
    # Configure reconnection with exponential backoff
    reconnect_delay = 5  # Initial reconnect delay in seconds
    max_reconnect_delay = 60  # Maximum reconnect delay in seconds
    
    while True:
        try:
            # Check if this thread should exit (newer thread has taken over)
            if not hasattr(st.session_state, 'ws_state') or st.session_state.ws_state.session_id != session_id:
                add_debug_msg(f"Thread {thread_id} detected it's been replaced, exiting")
                return
            
            # Create WebSocket with optimized settings
            ws = websocket.WebSocketApp(
                ws_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                # Set additional options to improve reliability
                header={"User-Agent": "Crypto Dashboard v1.0"}
            )
            
            # Set as the active instance
            if hasattr(st.session_state, 'ws_state'):
                st.session_state.ws_state.set_instance(ws)
            debug_message_queue.put(f"ws_instance_created:{id(ws)}")
            
            # Log connection attempt
            add_debug_msg(f"Connecting to {ws_url} [Session: {session_id}]")
            
            # Run WebSocket with optimized settings
            ws.run_forever(
                ping_interval=30,
                ping_timeout=10,
                ping_payload='{"method":"ping"}',
                reconnect=0  # We handle reconnection ourselves
            )
            
            # If we get here, connection closed or failed
            add_debug_msg(f"WebSocket run_forever ended, waiting {reconnect_delay}s before reconnecting")
            
            # Check if connection was ever established
            if getattr(current_thread, 'connection_open', False):
                debug_message_queue.put("ws_disconnected:closed")
                current_thread.connection_open = False
            
            # Wait before reconnecting with exponential backoff
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            
        except Exception as e:
            error_msg = f"WebSocket thread error: {str(e)}"
            add_debug_msg(error_msg)
            print(traceback.format_exc())
            
            # Wait before retrying
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

def start_websocket():
    """
    Start WebSocket connection with improved thread management
    """
    # Initialize WebSocket state if needed
    if 'ws_state' not in st.session_state:
        st.session_state.ws_state = WebSocketState()
    
    # Generate new session ID
    session_id = st.session_state.ws_state.new_session_id()
    
    # Log the new connection attempt
    add_debug_msg(f"Starting new WebSocket connection session: {session_id}")
    
    # Create and start thread with the new session ID
    ws_thread = threading.Thread(target=run_websocket, args=(session_id,), daemon=True)
    ws_thread.start()
    
    # Update state with the new thread
    st.session_state.ws_state.set_thread(ws_thread)
    
    return f"Starting new WebSocket connection (attempt #{st.session_state.ws_state.reconnect_attempt})"

def process_status_messages():
    """
    Process connection status messages with improved state handling
    """
    # Initialize state if needed
    if 'ws_state' not in st.session_state:
        st.session_state.ws_state = WebSocketState()
    
    # Track if we've processed any messages
    queue_changed = False
    connection_status_changed = False
    
    # Process all available messages
    while not debug_message_queue.empty():
        try:
            msg = debug_message_queue.get_nowait()
            queue_changed = True
            
            # Process connection status messages
            if msg.startswith("ws_connected:"):
                st.session_state.ws_state.mark_connected()
                st.session_state.ws_status = "Connected"
                connection_status_changed = True
                add_debug_msg(f"Connection status updated to Connected (reason: {msg.split(':')[1]})")
            
            elif msg.startswith("ws_disconnected:"):
                st.session_state.ws_state.mark_disconnected()
                st.session_state.ws_status = "Disconnected"
                connection_status_changed = True
                add_debug_msg(f"Connection status updated to Disconnected (reason: {msg.split(':')[1]})")
            
            elif msg.startswith("ws_instance_created:"):
                instance_id = msg.split(':')[1]
                add_debug_msg(f"New WebSocket instance created: {instance_id}")
            
            elif msg.startswith("last_message_time:"):
                timestamp = float(msg.split(':')[1])
                st.session_state['last_message_time'] = timestamp
            
            else:
                # Regular debug message
                if len(st.session_state.debug_msgs) >= 100:
                    st.session_state.debug_msgs.pop(0)  # Keep queue size limited
                st.session_state.debug_msgs.append(msg)
                
        except queue.Empty:
            break
        except Exception as e:
            print(f"Error processing debug message: {str(e)}")
            print(traceback.format_exc())
    
    # Infer connection status from message flow if needed
    if not st.session_state.ws_state.connected and ws_message_queue.qsize() > 0:
        st.session_state.ws_state.mark_connected()
        st.session_state.ws_status = "Connected"
        connection_status_changed = True
        add_debug_msg("Connection status automatically updated to Connected based on active message flow")
    
    return queue_changed, connection_status_changed

def process_websocket_messages():
    """
    Process WebSocket messages with improved performance and error handling
    """
    # First process any status messages
    process_status_messages()
    
    # Get connection state
    if 'ws_state' not in st.session_state:
        st.session_state.ws_state = WebSocketState()
    
    # Get queue size and adjust processing limit accordingly
    queue_size = ws_message_queue.qsize()
    
    # Dynamically adjust processing limit based on queue size
    # Process more messages when queue is large, but maintain a reasonable limit
    process_limit = min(1000, max(100, queue_size // 2))
    
    # Initialize counters
    processed_count = 0
    trade_count = 0
    orderbook_count = 0
    other_count = 0
    
    # Log processing start if queue is not empty
    if queue_size > 0:
        add_debug_msg(f"Processing WebSocket queue (size: {queue_size}, limit: {process_limit})")
    
    # Process messages up to the limit
    start_time = time.time()
    while not ws_message_queue.empty() and processed_count < process_limit:
        try:
            # Get message from queue
            message = ws_message_queue.get_nowait()
            processed_count += 1
            
            # Get message channel
            channel = message.get('channel', 'unknown')
            
            # Process by channel type
            if channel == 'l2Book' and 'data' in message:
                orderbook_count += 1
                data = message['data']
                instrument = data.get('coin')
                
                if instrument in instruments:
                    st.session_state.order_books[instrument] = data
            
            elif channel == 'trades' and 'data' in message:
                trades = message['data']
                
                for trade in trades:
                    instrument = trade.get('coin')
                    
                    if instrument in instruments:
                        # Convert A/B to Sell/Buy
                        side_code = trade.get('side')
                        side = "Sell" if side_code == "A" else "Buy" if side_code == "B" else side_code
                        
                        # Skip if missing data
                        if 'sz' not in trade or 'px' not in trade:
                            continue
                        
                        trade_count += 1
                        size = float(trade.get('sz', 0))
                        price = float(trade.get('px', 0))
                        notional = size * price
                        
                        # Record large trades (over $50,000)
                        if notional > 50000:
                            trade_time = datetime.fromtimestamp(int(trade.get('time', time.time() * 1000)) / 1000)
                            
                            # Add to trades list with bounded queue size
                            new_trade = {
                                'instrument': instrument,
                                'side': side,
                                'size': size,
                                'price': price,
                                'notional': notional,
                                'time': trade_time
                            }
                            
                            # Maintain limited trade history with thread-safe approach
                            if not hasattr(st.session_state, 'trades'):
                                st.session_state.trades = []
                            
                            st.session_state.trades.append(new_trade)
                            
                            # Limit list size to avoid memory issues
                            if len(st.session_state.trades) > 100:
                                st.session_state.trades = st.session_state.trades[-100:]
            
            elif channel == 'allMids' and 'data' in message:
                # Process all mids data (updates to latest prices)
                mids_data = message['data'].get('mids', {})
                for coin, price in mids_data.items():
                    if coin in instruments:
                        if 'latest_prices' not in st.session_state:
                            st.session_state.latest_prices = {}
                        st.session_state.latest_prices[coin] = float(price)
                other_count += 1
            
            elif channel == 'pong':
                # Just count pong messages
                other_count += 1
            
            else:
                # Count other message types
                other_count += 1
        
        except queue.Empty:
            # Queue emptied during processing
            break
        except Exception as e:
            # Log error but continue processing
            error_msg = f"Error processing message: {str(e)}"
            print(error_msg)
            print(traceback.format_exc())
            add_debug_msg(error_msg)
    
    # Calculate processing time
    processing_time = time.time() - start_time
    
    # Log processing summary when significant activity occurred
    if processed_count > 0:
        processing_rate = processed_count / processing_time if processing_time > 0 else 0
        summary = (f"Processed {processed_count} messages in {processing_time:.2f}s "
                   f"({processing_rate:.1f}/s): {trade_count} trades, "
                   f"{orderbook_count} orderbooks, {other_count} other")
        
        # Only log if we processed a significant number of messages
        if processed_count > 10 or trade_count > 0:
            add_debug_msg(summary)
    
    # Return processing stats
    return (f"Processed: {processed_count} messages "
            f"({trade_count} trades, {orderbook_count} orderbooks, {other_count} other)"), processed_count

def ensure_websocket_connection():
    """
    Check WebSocket connection status and reconnect if needed
    with improved decision logic
    """
    # First, process any status messages
    queue_changed, status_changed = process_status_messages()
    
    # Get current state
    if 'ws_state' not in st.session_state:
        st.session_state.ws_state = WebSocketState()
    
    status = st.session_state.ws_state.get_status()
    
    # Skip further checks if status just changed to connected
    if status_changed and status['connected']:
        return "Connection status updated, monitoring"
    
    # Evaluate connection health
    need_reconnect = False
    reconnect_reason = ""
    
    # Check if thread is alive
    if not status['thread_alive']:
        need_reconnect = True
        reconnect_reason = "Thread not alive"
    
    # Check if connection flag indicates disconnected state
    elif not status['connected'] and ws_message_queue.qsize() == 0:
        need_reconnect = True
        reconnect_reason = "Not connected according to flag and no queued messages"
    
    # Check for message flow timeout (no messages for 2 minutes)
    elif status['last_message_time'] > 0 and status['time_since_message'] > 120 and ws_message_queue.qsize() == 0:
        need_reconnect = True
        reconnect_reason = f"No messages in {status['time_since_message']:.1f} seconds and empty queue"
    
    # Reconnect if needed
    if need_reconnect:
        add_debug_msg(f"Reconnection needed: {reconnect_reason}")
        return start_websocket()
    
    # Ping if connected but no recent messages (heartbeat)
    elif status['connected'] and status['thread_alive'] and status['time_since_message'] > 60:
        add_debug_msg("Sending heartbeat ping due to message inactivity")
        try:
            if hasattr(st.session_state.ws_state, 'instance') and st.session_state.ws_state.instance:
                try:
                    st.session_state.ws_state.instance.send(json.dumps({"method": "ping"}))
                    return "Heartbeat ping sent"
                except:
                    add_debug_msg("Failed to send ping - connection may be stale")
                    return start_websocket()
        except:
            add_debug_msg("No valid WebSocket instance to ping")
    
    return "WebSocket connection appears healthy"

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

# Layout
st.title("Crypto Market Data Dashboard")
st.markdown("Real-time monitoring of order books and funding rates")

# WebSocket connection status indicator in sidebar
if 'ws_state' in st.session_state and st.session_state.ws_state.connected:
    st.sidebar.success("WebSocket Connected")
else:
    st.sidebar.error("WebSocket Disconnected")
    if st.sidebar.button("Connect WebSocket", key="sidebar_connect"):
        start_websocket()
        st.sidebar.info("Connecting...")

# Create tabs
tab1, tab2, tab3, tab4 = st.tabs(["Order Books", "Funding Rates", "Recent Trades", "Debug"])

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
        if 'ws_state' in st.session_state and st.session_state.ws_state.connected:
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
    
    # Connection Status Summary
    st.subheader("Connection Status Summary")
    status_cols = st.columns(3)

    # WS Connected Flag
    with status_cols[0]:
        if 'ws_state' in st.session_state and st.session_state.ws_state.connected:
            st.success("Connection Flag: True")
        else:
            st.error("Connection Flag: False")

    # Thread Status
    with status_cols[1]:
        thread_alive = False
        thread_id = None
        if 'ws_state' in st.session_state and st.session_state.ws_state.thread is not None:
            thread_alive = st.session_state.ws_state.thread.is_alive()
            thread_id = st.session_state.ws_state.thread.ident
        
        if thread_alive:
            st.success(f"Thread: Alive (ID: {thread_id})")
        else:
            st.error("Thread: Not alive")

    # Queue Status 
    with status_cols[2]:
        queue_size = ws_message_queue.qsize()
        if queue_size > 0:
            st.success(f"Message Queue: {queue_size} messages")
        else:
            st.warning("Message Queue: Empty")
    
    # Detailed diagnostics and actions
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("WebSocket Status")
        if 'ws_state' in st.session_state:
            status = st.session_state.ws_state.get_status()
            st.write(f"Connected: {status['connected']}")
            st.write(f"Thread Alive: {status['thread_alive']}")
            st.write(f"Message Queue Size: {ws_message_queue.qsize()}")
            st.write(f"Debug Queue Size: {debug_message_queue.qsize()}")
            st.write(f"Session ID: {status['session_id']}")
            st.write(f"Reconnect Attempts: {status['reconnect_attempt']}")
            
            if status['last_message_time'] > 0:
                last_message_time = datetime.fromtimestamp(status['last_message_time']).strftime('%H:%M:%S')
                st.write(f"Last Message: {last_message_time} ({status['time_since_message']:.1f}s ago)")
        
        # Normal reconnect button
        if st.button("Force Reconnect", key="debug_reconnect"):
            # Start a new connection
            add_debug_msg("Manual reconnect initiated")
            start_websocket()
            st.success("Reconnecting...")
    
    # Add the complete reset button
    if st.button("Force Complete Reset", key="complete_reset"):
        # Reset connection flags
        st.session_state.ws_state = WebSocketState()
        st.session_state.ws_status = "Disconnected"
        
        # Clear message queues
        while not ws_message_queue.empty():
            try:
                ws_message_queue.get_nowait()
            except:
                pass
        
        while not debug_message_queue.empty():
            try:
                debug_message_queue.get_nowait()
            except:
                pass
        
        add_debug_msg("Performed complete reset of all WebSocket state")
        st.success("Complete reset done - connection will be reestablished on next refresh")
        
    # Add a manual ping button
    if st.button("Send Ping", key="send_ping"):
        if 'ws_state' in st.session_state and st.session_state.ws_state.connected and st.session_state.ws_state.instance:
            try:
                # Send ping directly
                st.session_state.ws_state.instance.send(json.dumps({"method": "ping"}))
                add_debug_msg("Manual ping sent")
                st.success("Ping sent successfully!")
            except Exception as e:
                add_debug_msg(f"Error sending ping: {str(e)}")
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
        
    # Show message processing statistics
    st.subheader("Message Processing Statistics")
    stats_cols = st.columns(2)
    
    with stats_cols[0]:
        if 'processed_stats' not in st.session_state:
            st.session_state.processed_stats = []
        
        # Display processed messages over time
        if len(st.session_state.processed_stats) > 0:
            st.write("Messages processed per refresh:")
            stats_df = pd.DataFrame(st.session_state.processed_stats)
            st.line_chart(stats_df.set_index('time')['count'])
        else:
            st.write("No message processing statistics yet")
    
    with stats_cols[1]:
        # Add a message capture button
        if st.button("Capture Raw Messages", key="capture_messages"):
            # Get a few messages from the queue for analysis
            captured = []
            for i in range(min(5, ws_message_queue.qsize())):
                try:
                    message = ws_message_queue.get_nowait()
                    captured.append(message)
                    # Put it back at the end
                    ws_message_queue.put(message)
                except:
                    break
            
            if captured:
                add_debug_msg(f"Captured {len(captured)} messages for analysis")
                st.session_state.captured_messages = captured
                st.success(f"Captured {len(captured)} messages")
            else:
                st.warning("No messages available to capture")
        
        # Display captured messages
        if 'captured_messages' in st.session_state and st.session_state.captured_messages:
            st.write("Captured messages:")
            st.json(st.session_state.captured_messages)

# Check connection and perform heartbeat if needed
current_time = datetime.now()
if 'last_heartbeat' in st.session_state:
    time_since_heartbeat = (current_time - st.session_state.last_heartbeat).total_seconds()
    
    # Perform heartbeat every 30 seconds
    if time_since_heartbeat > 30:
        heartbeat_result = ensure_websocket_connection()
        st.sidebar.text(heartbeat_result)
        st.session_state.last_heartbeat = current_time

# Process WebSocket messages on each refresh
result, count = process_websocket_messages()

# Store stats for visualization
if 'processed_stats' not in st.session_state:
    st.session_state.processed_stats = []
    
# Add stats for this refresh cycle  
st.session_state.processed_stats.append({
    'time': datetime.now().strftime('%H:%M:%S'),
    'count': count
})

# Keep only the most recent stats
if len(st.session_state.processed_stats) > 30:
    st.session_state.processed_stats = st.session_state.processed_stats[-30:]

st.sidebar.text(result)

# Start WebSocket connection on initial load if not already started
if 'ws_state' in st.session_state and not st.session_state.ws_state.connected:
    start_websocket()

# Initial data load if needed
if not st.session_state.order_books or not st.session_state.funding_rates:
    update_data()

# Set up automatic refresh
if st.sidebar.checkbox("Auto-refresh", value=True, key="auto_refresh"):
    refresh_rate = st.sidebar.slider("Refresh rate (sec)", 1, 30, 5, key="refresh_rate")
    st.sidebar.write(f"Dashboard will refresh every {refresh_rate} seconds")
    
    # Add WebSocket message processing stats to sidebar
    if 'ws_state' in st.session_state and st.session_state.ws_state.connected:
        st.sidebar.success(f"WebSocket connected | Queue: {ws_message_queue.qsize()}")
    else:
        st.sidebar.error("WebSocket disconnected")
    
    # Sleep and rerun
    time.sleep(refresh_rate)
    st.rerun()