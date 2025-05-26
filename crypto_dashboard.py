import asyncio
import json
import logging
import time
import hmac
import hashlib
import base64
from typing import Dict, List, Any, Optional, Callable
import requests
import websockets
from datetime import datetime, timedelta
import threading
import queue
import sys
import os
import websocket  # websocket-client library
import traceback
import uuid
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go

# Set page configuration
st.set_page_config(
    page_title="Crypto Arbitrage Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Simple HTTP client for Hyperliquid API
class SimpleHyperliquidClient:
    """Simple HTTP client for Hyperliquid API endpoints"""
    
    def __init__(self):
        self.base_url = "https://api.hyperliquid.xyz"
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def l2_snapshot(self, coin: str):
        """Get L2 order book snapshot"""
        try:
            response = self.session.post(
                f"{self.base_url}/info",
                json={"type": "l2Book", "coin": coin},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching order book for {coin}: {str(e)}")
            return None
    
    def funding_history(self, coin: str, start_time: int, end_time: int):
        """Get funding history for a coin"""
        try:
            response = self.session.post(
                f"{self.base_url}/info",
                json={
                    "type": "fundingHistory",
                    "coin": coin,
                    "startTime": start_time,
                    "endTime": end_time
                },
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching funding history for {coin}: {str(e)}")
            return []
    
    def all_mids(self):
        """Get all mid prices"""
        try:
            response = self.session.post(
                f"{self.base_url}/info",
                json={"type": "allMids"},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            st.error(f"Error fetching mid prices: {str(e)}")
            return {}

# Create thread-safe queues
ws_message_queue = queue.Queue(maxsize=10000)
debug_message_queue = queue.Queue(maxsize=1000)

# Thread-safe WebSocket state manager
class WebSocketState:
    """Thread-safe WebSocket state manager"""
    def __init__(self):
        self.connected = False
        self.last_message_time = 0
        self.last_ping_time = 0
        self.thread = None
        self.ws_instance = None
        self.session_id = str(uuid.uuid4())[:8]
        self.reconnect_attempt = 0
        self.lock = threading.Lock()
        self.should_run = True
        self.subscriptions = {}
        self.connection_type = "none"  # Track which connection type is active
    
    def mark_connected(self, connection_type="websocket-client"):
        with self.lock:
            self.connected = True
            self.last_message_time = time.time()
            self.connection_type = connection_type
            return self.connected
    
    def mark_disconnected(self):
        with self.lock:
            self.connected = False
            self.connection_type = "none"
            return self.connected
    
    def update_last_message_time(self):
        with self.lock:
            self.last_message_time = time.time()
            return self.last_message_time
    
    def update_ping_time(self):
        with self.lock:
            self.last_ping_time = time.time()
            return self.last_ping_time
    
    def set_thread(self, thread):
        with self.lock:
            self.thread = thread
            return self.thread
    
    def set_instance(self, instance):
        with self.lock:
            self.ws_instance = instance
            return self.ws_instance
    
    def new_session(self):
        with self.lock:
            self.session_id = str(uuid.uuid4())[:8]
            self.reconnect_attempt += 1
            self.should_run = True
            self.subscriptions = {}
            return self.session_id
    
    def stop(self):
        with self.lock:
            self.should_run = False
            self.connected = False
    
    def get_status(self):
        with self.lock:
            thread_alive = self.thread is not None and self.thread.is_alive()
            time_since_message = time.time() - self.last_message_time if self.last_message_time > 0 else float('inf')
            time_since_ping = time.time() - self.last_ping_time if self.last_ping_time > 0 else float('inf')
            
            return {
                'connected': self.connected,
                'thread_alive': thread_alive,
                'last_message_time': self.last_message_time,
                'time_since_message': time_since_message,
                'time_since_ping': time_since_ping,
                'session_id': self.session_id,
                'reconnect_attempt': self.reconnect_attempt,
                'should_run': self.should_run,
                'subscriptions': len(self.subscriptions),
                'connection_type': self.connection_type
            }

def initialize_dashboard_state():
    """Initialize all dashboard state variables with proper defaults"""
    if 'ws_state' not in st.session_state:
        st.session_state.ws_state = WebSocketState()
    
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
    if 'use_alternative_ws' not in st.session_state:
        st.session_state.use_alternative_ws = False

# Initialize dashboard state
initialize_dashboard_state()

# Create global HTTP client
@st.cache_resource
def get_http_client():
    """Get cached HTTP client"""
    return SimpleHyperliquidClient()

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

# Trade threshold setting
trade_threshold = st.sidebar.slider(
    "Trade Threshold ($)",
    min_value=100,
    max_value=100000,
    value=1000,  # Lowered from 50000 to 1000
    step=500,
    help="Show trades larger than this amount"
)

def add_debug_msg(msg):
    """Add debug message to queue with thread safety"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    full_msg = f"{timestamp}: {msg}"
    
    try:
        debug_message_queue.put(full_msg, block=False)
    except queue.Full:
        try:
            for _ in range(10):
                debug_message_queue.get_nowait()
            debug_message_queue.put(full_msg, block=False)
        except:
            pass
    
    print(full_msg)

def test_simple_websocket():
    """Simple WebSocket test with enhanced debugging"""
    add_debug_msg("Starting enhanced simple WebSocket test...")
    
    def simple_ws_test():
        try:
            import websocket
            
            # Enable WebSocket debugging
            websocket.enableTrace(True)
            
            message_count = 0
            
            def on_message(ws, message):
                nonlocal message_count
                message_count += 1
                add_debug_msg(f"Simple test message {message_count}: {message[:150]}")
                
                try:
                    if message != "Websocket connection established.":
                        data = json.loads(message)
                        ws_message_queue.put(data, block=False)
                        add_debug_msg(f"Simple test: Queued message {message_count}")
                        
                        # Mark connection as active when we receive data
                        if 'ws_state' in st.session_state:
                            st.session_state.ws_state.mark_connected("simple-test")
                except Exception as e:
                    add_debug_msg(f"Simple test: Error processing message: {str(e)}")
            
            def on_open(ws):
                add_debug_msg("🎉 Simple test WebSocket opened successfully!")
                
                try:
                    # Send allMids subscription
                    allmids_sub = {
                        "method": "subscribe",
                        "subscription": {"type": "allMids"}
                    }
                    ws.send(json.dumps(allmids_sub))
                    add_debug_msg("✅ Simple test: Sent allMids subscription")
                    
                    # Send trades subscriptions for all instruments
                    for instrument in ['BTC', 'ETH', 'SOL']:
                        trades_sub = {
                            "method": "subscribe", 
                            "subscription": {"type": "trades", "coin": instrument}
                        }
                        ws.send(json.dumps(trades_sub))
                        add_debug_msg(f"✅ Simple test: Sent {instrument} trades subscription")
                        
                        # Send L2 book subscription
                        book_sub = {
                            "method": "subscribe",
                            "subscription": {"type": "l2Book", "coin": instrument}
                        }
                        ws.send(json.dumps(book_sub))
                        add_debug_msg(f"✅ Simple test: Sent {instrument} L2Book subscription")
                    
                    add_debug_msg("🚀 Simple test: All subscriptions sent!")
                    
                except Exception as e:
                    add_debug_msg(f"❌ Simple test: Error sending subscriptions: {str(e)}")
            
            def on_error(ws, error):
                add_debug_msg(f"❌ Simple test error: {str(error)}")
                add_debug_msg(f"Error type: {type(error).__name__}")
            
            def on_close(ws, close_status_code, close_msg):
                add_debug_msg(f"🔴 Simple test closed: {close_msg} (code: {close_status_code})")
            
            add_debug_msg("Creating simple WebSocket connection...")
            
            # Create simple WebSocket
            ws = websocket.WebSocketApp(
                "wss://api.hyperliquid.xyz/ws",
                on_message=on_message,
                on_open=on_open,
                on_error=on_error,
                on_close=on_close
            )
            
            add_debug_msg("Starting simple WebSocket run_forever...")
            
            # Run WebSocket with timeout
            ws.run_forever(
                ping_interval=60,
                ping_timeout=10,
                skip_utf8_validation=True
            )
            
        except Exception as e:
            add_debug_msg(f"❌ Simple test exception: {str(e)}")
            add_debug_msg(f"Exception traceback: {traceback.format_exc()}")
    
    # Run in thread
    add_debug_msg("Starting simple test thread...")
    thread = threading.Thread(target=simple_ws_test, daemon=True)
    thread.start()
    
    return "Started enhanced simple WebSocket test"

# Original WebSocket implementation with enhanced debugging
class HyperliquidWebSocketManager:
    """WebSocket manager for Hyperliquid API with enhanced debugging"""
    
    def __init__(self, instruments, ws_state):
        self.instruments = instruments
        self.ws_state = ws_state
        self.ws = None
        self.ping_thread = None
        self.stop_event = threading.Event()
        self.subscription_id_counter = 0
        self.ws_url = "wss://api.hyperliquid.xyz/ws"
        
    def start(self):
        """Start the WebSocket connection with enhanced debugging"""
        # Enable WebSocket debugging
        websocket.enableTrace(True)
        
        self.stop_event.clear()
        self.ws_state.new_session()
        
        add_debug_msg(f"Starting Hyperliquid WebSocket connection to {self.ws_url}")
        add_debug_msg(f"Selected instruments: {self.instruments}")
        
        # Create WebSocket with proper handlers
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        
        # Update WebSocket instance in state
        self.ws_state.set_instance(self.ws)
        
        # Start ping thread
        self.ping_thread = threading.Thread(target=self.send_ping_loop, daemon=True)
        self.ping_thread.start()
        
        # Run WebSocket with better error handling
        try:
            add_debug_msg("Starting WebSocket run_forever...")
            self.ws.run_forever(
                ping_interval=0,
                ping_timeout=10,
                reconnect=0,
                skip_utf8_validation=True,
                http_proxy_host=None,
                http_proxy_port=None
            )
            add_debug_msg("WebSocket run_forever completed normally")
        except Exception as e:
            add_debug_msg(f"WebSocket run_forever error: {str(e)}")
            add_debug_msg(f"Error type: {type(e).__name__}")
            add_debug_msg(f"Error details: {repr(e)}")
            add_debug_msg(traceback.format_exc())
        finally:
            add_debug_msg("WebSocket cleanup started")
            self.cleanup()
    
    def stop(self):
        """Stop the WebSocket connection"""
        add_debug_msg("Stopping WebSocket connection")
        self.ws_state.stop()
        self.stop_event.set()
        
        if self.ws:
            self.ws.close()
        
        self.cleanup()
    
    def cleanup(self):
        """Clean up resources"""
        self.ws_state.mark_disconnected()
        if self.ping_thread and self.ping_thread.is_alive():
            self.ping_thread.join(timeout=1)
    
    def on_open(self, ws):
        """Handle WebSocket connection open"""
        add_debug_msg("WebSocket connection opened successfully")
        self.ws_state.mark_connected("websocket-client")
        
        # Wait a moment for connection to stabilize
        time.sleep(0.5)
        
        # Subscribe to channels
        try:
            self.subscribe_to_channels()
        except Exception as e:
            add_debug_msg(f"Error during initial subscription: {str(e)}")
    
    def on_message(self, ws, message):
        """Handle incoming WebSocket messages with enhanced debugging"""
        try:
            # Debug: Log raw message (first 10 messages only)
            if not hasattr(self, 'message_count'):
                self.message_count = 0
            
            self.message_count += 1
            if self.message_count <= 10:
                add_debug_msg(f"Raw WebSocket message {self.message_count}: {message[:200]}")
            
            # Handle connection confirmation message
            if message == "Websocket connection established.":
                add_debug_msg("Received connection establishment confirmation")
                return
            
            # Update last message time
            self.ws_state.update_last_message_time()
            
            # Parse JSON message
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                add_debug_msg(f"Non-JSON message: {message[:100]}")
                return
            
            # Handle pong responses
            if data.get('channel') == 'pong':
                add_debug_msg("Received pong response")
                return
            
            # Debug: Log message type
            if self.message_count <= 20:
                add_debug_msg(f"Parsed message {self.message_count}: {data.get('channel', 'unknown')} channel")
            
            # Queue message for processing
            try:
                ws_message_queue.put(data, block=False)
            except queue.Full:
                # If queue is full, remove old messages
                try:
                    for _ in range(100):
                        ws_message_queue.get_nowait()
                    ws_message_queue.put(data, block=False)
                    add_debug_msg("Queue was full, cleared old messages")
                except:
                    add_debug_msg("Failed to clear queue, dropping message")
            
        except Exception as e:
            add_debug_msg(f"Error in on_message: {str(e)}")
            add_debug_msg(traceback.format_exc())
    
    def on_error(self, ws, error):
        """Handle WebSocket errors with enhanced debugging"""
        add_debug_msg(f"WebSocket error: {str(error)}")
        add_debug_msg(f"Error type: {type(error).__name__}")
        add_debug_msg(f"Error details: {repr(error)}")
        self.ws_state.mark_disconnected()
    
    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close"""
        add_debug_msg(f"WebSocket closed: {close_msg} (code: {close_status_code})")
        self.ws_state.mark_disconnected()
    
    def send_ping_loop(self):
        """Send periodic pings to keep connection alive"""
        add_debug_msg("Ping loop started")
        
        while not self.stop_event.wait(50):  # 50 second interval
            if not self.ws or not self.ws_state.should_run:
                add_debug_msg("Ping loop stopping - no WebSocket or should_run is False")
                break
            
            try:
                ping_msg = json.dumps({"method": "ping"})
                self.ws.send(ping_msg)
                self.ws_state.update_ping_time()
                add_debug_msg("Ping sent to server")
            except Exception as e:
                add_debug_msg(f"Error sending ping: {str(e)}")
                break
        
        add_debug_msg("Ping loop stopped")
    
    def subscribe_to_channels(self):
        """Subscribe to all required channels with enhanced debugging"""
        subscription_count = 0
        
        add_debug_msg(f"Starting subscriptions for instruments: {self.instruments}")
        
        # Subscribe to allMids first
        try:
            allmids_sub = {
                "method": "subscribe",
                "subscription": {"type": "allMids"}
            }
            self.ws.send(json.dumps(allmids_sub))
            subscription_count += 1
            add_debug_msg("Subscribed to allMids")
            time.sleep(0.1)
        except Exception as e:
            add_debug_msg(f"Error subscribing to allMids: {str(e)}")
        
        # Subscribe to instrument-specific channels
        for instrument in self.instruments:
            try:
                # L2 Book subscription
                book_sub = {
                    "method": "subscribe",
                    "subscription": {"type": "l2Book", "coin": instrument}
                }
                self.ws.send(json.dumps(book_sub))
                subscription_count += 1
                add_debug_msg(f"Subscribed to l2Book for {instrument}")
                time.sleep(0.1)
                
                # Trades subscription
                trades_sub = {
                    "method": "subscribe",
                    "subscription": {"type": "trades", "coin": instrument}
                }
                self.ws.send(json.dumps(trades_sub))
                subscription_count += 1
                add_debug_msg(f"Subscribed to trades for {instrument}")
                time.sleep(0.1)
                
            except Exception as e:
                add_debug_msg(f"Error subscribing to {instrument}: {str(e)}")
        
        add_debug_msg(f"Completed {subscription_count} subscription requests")
        
        # Store subscription info
        with self.ws_state.lock:
            self.ws_state.subscriptions = {
                'allmids': True,
                'instruments': self.instruments.copy()
            }

def run_websocket_thread(instruments, ws_state):
    """Run WebSocket in a separate thread with improved error handling"""
    thread_id = threading.get_ident()
    add_debug_msg(f"WebSocket thread {thread_id} started")
    
    reconnect_delay = 5
    max_reconnect_delay = 60
    
    while ws_state.should_run:
        try:
            add_debug_msg("Creating new WebSocket manager instance")
            ws_manager = HyperliquidWebSocketManager(instruments, ws_state)
            
            add_debug_msg("Starting WebSocket connection...")
            ws_manager.start()
            
            # If we reach here, the connection ended
            add_debug_msg("WebSocket connection ended, checking if should reconnect")
            
            if not ws_state.should_run:
                add_debug_msg("WebSocket thread told to stop, exiting")
                break
                
            # Wait before reconnecting
            add_debug_msg(f"Will reconnect in {reconnect_delay} seconds")
            time.sleep(reconnect_delay)
            
            # Exponential backoff
            reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            
        except Exception as e:
            add_debug_msg(f"WebSocket thread error: {str(e)}")
            add_debug_msg(traceback.format_exc())
            
            if not ws_state.should_run:
                break
                
            # Wait before retrying
            add_debug_msg(f"Error occurred, will retry in {reconnect_delay} seconds")
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
    
    add_debug_msg(f"WebSocket thread {thread_id} ended")

def start_websocket(use_alternative=False):
    """Start WebSocket connection - simplified version"""
    if 'ws_state' not in st.session_state:
        st.session_state.ws_state = WebSocketState()
    
    # Stop existing connection if any
    if st.session_state.ws_state.thread and st.session_state.ws_state.thread.is_alive():
        add_debug_msg("Stopping existing WebSocket connection")
        st.session_state.ws_state.stop()
        if st.session_state.ws_state.ws_instance:
            st.session_state.ws_state.ws_instance.close()
        time.sleep(1)
    
    # Start new connection
    session_id = st.session_state.ws_state.new_session()
    
    if use_alternative:
        add_debug_msg(f"Starting test WebSocket session: {session_id}")
        # Use simple test for now
        return test_simple_websocket()
    else:
        add_debug_msg(f"Starting standard WebSocket session: {session_id}")
        ws_thread = threading.Thread(
            target=run_websocket_thread,
            args=(instruments, st.session_state.ws_state),
            daemon=True
        )
        ws_thread.start()
        st.session_state.ws_state.set_thread(ws_thread)
    
    return f"Started WebSocket connection (session: {session_id})"

def process_websocket_messages():
    """Process WebSocket messages with improved error handling and debugging"""
    processed_count = 0
    trade_count = 0
    orderbook_count = 0
    other_count = 0
    large_trade_count = 0
    
    # Limit processing to prevent UI lag
    max_process = min(100, ws_message_queue.qsize())
    start_time = time.time()
    
    # Initialize summary
    summary = f"No messages processed (queue size: {ws_message_queue.qsize()})"
    
    while processed_count < max_process and time.time() - start_time < 0.5:
        try:
            message = ws_message_queue.get_nowait()
            processed_count += 1
            
            # Skip subscription confirmations
            if 'id' in message and 'result' in message:
                other_count += 1
                continue
            
            channel = message.get('channel', 'unknown')
            
            # Debug first few messages
            if processed_count <= 5:
                add_debug_msg(f"Processing message {processed_count}: {channel} channel")
            
            # Process order book updates
            if channel == 'l2Book' and 'data' in message:
                orderbook_count += 1
                data = message['data']
                instrument = data.get('coin')
                
                if instrument in instruments:
                    st.session_state.order_books[instrument] = data
            
            # Process trade updates
            elif channel == 'trades' and 'data' in message:
                trades = message['data']
                if not trades:
                    continue
                
                # Debug trade messages
                if trade_count < 3:
                    add_debug_msg(f"Received {len(trades)} trades: {[t.get('coin') for t in trades]}")
                
                for trade in trades:
                    instrument = trade.get('coin')
                    
                    if instrument in instruments:
                        trade_count += 1
                        
                        # Convert side codes
                        side_code = trade.get('side')
                        side = "Sell" if side_code == "A" else "Buy" if side_code == "B" else side_code
                        
                        # Validate required fields
                        if 'sz' not in trade or 'px' not in trade:
                            continue
                        
                        try:
                            size = float(trade['sz'])
                            price = float(trade['px'])
                            notional = size * price
                            
                            # Record trades above threshold (now configurable)
                            if notional > trade_threshold:
                                large_trade_count += 1
                                trade_time = datetime.fromtimestamp(
                                    int(trade.get('time', time.time() * 1000)) / 1000
                                )
                                
                                # Debug large trades
                                if large_trade_count <= 3:
                                    add_debug_msg(f"Large trade: {instrument} {side} ${notional:.2f}")
                                
                                # Add to trades list (thread-safe)
                                if len(st.session_state.trades) >= 100:
                                    st.session_state.trades = st.session_state.trades[-50:]
                                
                                st.session_state.trades.append({
                                    'instrument': instrument,
                                    'side': side,
                                    'size': size,
                                    'price': price,
                                    'notional': notional,
                                    'time': trade_time
                                })
                                
                        except (ValueError, TypeError) as e:
                            add_debug_msg(f"Error parsing trade data: {str(e)}")
            
            # Process allMids updates
            elif channel == 'allMids' and 'data' in message:
                other_count += 1
                mids_data = message.get('data', {}).get('mids', {})
                
                for coin, price in mids_data.items():
                    if coin in instruments:
                        try:
                            st.session_state.latest_prices[coin] = float(price)
                        except (ValueError, TypeError):
                            pass
            
            else:
                other_count += 1
                
        except queue.Empty:
            break
        except Exception as e:
            add_debug_msg(f"Error processing message: {str(e)}")
    
    # Update connection status
    if processed_count > 0 and not st.session_state.ws_state.connected:
        st.session_state.ws_state.mark_connected()
        st.session_state.ws_status = "Connected"
    
    processing_time = time.time() - start_time
    
    # Create summary
    if processed_count > 0:
        summary = (f"Processed {processed_count} messages in {processing_time:.2f}s: "
                   f"{trade_count} trades ({large_trade_count} large), "
                   f"{orderbook_count} books, {other_count} other")
        if processed_count > 20 or large_trade_count > 0:
            add_debug_msg(summary)
    else:
        summary = f"No messages processed (queue: {ws_message_queue.qsize()}, time: {processing_time:.2f}s)"
    
    return summary, processed_count

def process_debug_messages():
    """Process debug messages and update session state"""
    new_messages = []
    
    while not debug_message_queue.empty():
        try:
            msg = debug_message_queue.get_nowait()
            new_messages.append(msg)
        except queue.Empty:
            break
    
    if new_messages:
        # Add to session state with size limit
        st.session_state.debug_msgs.extend(new_messages)
        if len(st.session_state.debug_msgs) > 200:
            st.session_state.debug_msgs = st.session_state.debug_msgs[-100:]
    
    return len(new_messages)

# Data fetching functions
@st.cache_data(ttl=update_interval)
def get_order_book(instrument):
    client = get_http_client()
    return client.l2_snapshot(instrument)

@st.cache_data(ttl=update_interval*2)
def get_funding_rate(instrument):
    client = get_http_client()
    try:
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

def update_data():
    for instrument in instruments:
        order_book = get_order_book(instrument)
        if order_book:
            st.session_state.order_books[instrument] = order_book
        
        funding_rate = get_funding_rate(instrument)
        if funding_rate:
            st.session_state.funding_rates[instrument] = funding_rate
    
    st.session_state.last_update = datetime.now()

# Main UI Layout
st.title("Crypto Market Data Dashboard")
st.markdown("Real-time monitoring of order books and funding rates")

# WebSocket status in sidebar
if 'ws_state' in st.session_state and st.session_state.ws_state.connected:
    status = st.session_state.ws_state.get_status()
    connection_type = status.get('connection_type', 'unknown')
    st.sidebar.success(f"WebSocket Connected ({connection_type})")
    st.sidebar.text(f"Messages: {status['time_since_message']:.1f}s ago")
    st.sidebar.text(f"Queue: {ws_message_queue.qsize()}")
elif ws_message_queue.qsize() > 0:
    # If we have messages in queue but state shows disconnected, show as receiving data
    st.sidebar.success(f"Receiving Data (Queue: {ws_message_queue.qsize()})")
else:
    st.sidebar.error("WebSocket Disconnected")
    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("Connect", key="sidebar_connect"):
            result = start_websocket(use_alternative=False)
            st.sidebar.info(result)
    with col2:
        if st.button("Simple Test", key="sidebar_simple_test"):
            result = test_simple_websocket()
            st.sidebar.info(result)

# Create tabs
tab1, tab2, tab3, tab4 = st.tabs(["Order Books", "Funding Rates", "Recent Trades", "Debug"])

# Order Books Tab
with tab1:
    st.header("Order Book Data")
    
    if st.button("Refresh Order Books", key="refresh_books"):
        update_data()
    
    st.text(f"Last updated: {st.session_state.last_update.strftime('%Y-%m-%d %H:%M:%S')}")
    
    cols = st.columns(len(instruments))
    
    for i, instrument in enumerate(instruments):
        with cols[i]:
            if instrument in st.session_state.order_books:
                order_book = st.session_state.order_books[instrument]
                
                if 'levels' in order_book and len(order_book['levels']) >= 2:
                    bids = order_book['levels'][0][:10]  # Top 10 bids
                    asks = order_book['levels'][1][:10]  # Top 10 asks
                    
                    # Calculate metrics
                    top_bid = float(bids[0]['px'])
                    top_ask = float(asks[0]['px'])
                    mid_price = (top_bid + top_ask) / 2
                    spread = top_ask - top_bid
                    spread_bps = (spread / mid_price) * 10000
                    
                    # Create enhanced dataframes
                    bid_df = pd.DataFrame(bids)
                    bid_df['px'] = pd.to_numeric(bid_df['px'])
                    bid_df['sz'] = pd.to_numeric(bid_df['sz'])
                    bid_df['total'] = bid_df['px'] * bid_df['sz']
                    bid_df = bid_df.sort_values('px', ascending=False)  # Highest bid first
                    
                    ask_df = pd.DataFrame(asks)
                    ask_df['px'] = pd.to_numeric(ask_df['px'])
                    ask_df['sz'] = pd.to_numeric(ask_df['sz'])
                    ask_df['total'] = ask_df['px'] * ask_df['sz']
                    ask_df = ask_df.sort_values('px', ascending=True)  # Lowest ask first
                    
                    # Compact header with all info in one line
                    st.markdown(f"""
                    <div style='background: #1f2937; padding: 8px 15px; border-radius: 6px; margin: 5px 0;
                                display: flex; justify-content: space-between; align-items: center;'>
                        <h4 style='color: #f3f4f6; margin: 0; font-size: 1.1em;'>{instrument}</h4>
                        <div style='color: #9ca3af; font-size: 0.9em;'>
                            <span style='color: #22c55e;'>Bid: ${top_bid:.4f}</span> | 
                            <span style='color: #ef4444;'>Ask: ${top_ask:.4f}</span> | 
                            <span style='color: #f59e0b;'>Spread: {spread_bps:.2f}bps</span> | 
                            <span style='color: #8b5cf6;'>Mid: ${mid_price:.4f}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Vertical order book layout: Asks above, Mid price in between, Bids below
                    
                    # Asks section (top)
                    st.markdown("**Asks (Sells)**")
                    ask_display = ask_df.copy()
                    ask_display.columns = ['Price', 'Size', 'Orders', 'Total']
                    ask_display = ask_display[['Price', 'Size', 'Total']].iloc[::-1]  # Reverse to show highest first
                    
                    # Display asks with red styling
                    st.dataframe(
                        ask_display.style.background_gradient(
                            subset=['Size'], 
                            cmap='Reds', 
                            vmin=0, 
                            vmax=ask_display['Size'].max()
                        ).format({
                            'Price': '${:.4f}',
                            'Size': '{:.1f}',
                            'Total': '${:.0f}'
                        }),
                        use_container_width=True,
                        height=150
                    )
                    
                    # Mid price section (center)
                    st.markdown(f"""
                    <div style='background: linear-gradient(90deg, #fbbf24, #f59e0b); 
                                padding: 10px; border-radius: 6px; margin: 10px 0;
                                text-align: center;'>
                        <div style='color: #1f2937; font-weight: bold; font-size: 1.2em;'>
                            Mid Price: ${mid_price:.4f}
                        </div>
                        <div style='color: #1f2937; font-size: 0.9em;'>
                            Spread: {spread_bps:.2f} bps (${spread:.4f})
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Bids section (bottom)
                    st.markdown("**Bids (Buys)**")
                    bid_display = bid_df.copy()
                    bid_display.columns = ['Price', 'Size', 'Orders', 'Total']
                    bid_display = bid_display[['Price', 'Size', 'Total']]
                    
                    # Display bids with green styling
                    st.dataframe(
                        bid_display.style.background_gradient(
                            subset=['Size'], 
                            cmap='Greens', 
                            vmin=0, 
                            vmax=bid_display['Size'].max()
                        ).format({
                            'Price': '${:.4f}',
                            'Size': '{:.1f}',
                            'Total': '${:.0f}'
                        }),
                        use_container_width=True,
                        height=150
                    )
                    
                    # Market Depth visualization
                    st.markdown("**Market Depth**")
                    
                    fig = go.Figure()
                    
                    # Add asks (red bars)
                    fig.add_trace(go.Bar(
                        x=ask_df['px'],
                        y=ask_df['sz'],
                        name='Asks',
                        marker_color='rgba(239, 68, 68, 0.8)',
                        opacity=0.8
                    ))
                    
                    # Add bids (green bars)
                    fig.add_trace(go.Bar(
                        x=bid_df['px'],
                        y=bid_df['sz'],
                        name='Bids',
                        marker_color='rgba(34, 197, 94, 0.8)',
                        opacity=0.8
                    ))
                    
                    # Add mid price line
                    fig.add_vline(
                        x=mid_price,
                        line=dict(color='#f59e0b', width=2, dash='dash'),
                        annotation_text=f"Mid: ${mid_price:.4f}"
                    )
                    
                    fig.update_layout(
                        title=None,  # Remove title to save space
                        xaxis_title="Price",
                        yaxis_title="Size",
                        barmode='group',
                        height=250,  # Smaller height
                        template="plotly_dark",
                        showlegend=False,  # Remove legend to save space
                        margin=dict(l=0, r=0, t=20, b=0)
                    )
                    
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning(f"No valid order book data for {instrument}")
            else:
                st.info(f"Loading order book data for {instrument}...")

# Funding Rates Tab
with tab2:
    st.header("Funding Rate Data")
    
    if st.button("Refresh Funding Rates", key="refresh_funding"):
        update_data()
    
    funding_data = []
    for instrument in instruments:
        if instrument in st.session_state.funding_rates:
            funding = st.session_state.funding_rates[instrument]
            funding_data.append({
                'Instrument': instrument,
                'Rate': funding['rate'] * 100,
                'Annualized': funding['rate'] * 3 * 365 * 100,
                'Next Funding': datetime.fromtimestamp(funding['next_funding_time']),
                'Time to Next': (datetime.fromtimestamp(funding['next_funding_time']) - datetime.now()).total_seconds() / 3600
            })
    
    if funding_data:
        funding_df = pd.DataFrame(funding_data)
        
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
        
        # Visualization
        st.subheader("Funding Rate Comparison")
        fig = go.Figure()
        
        fig.add_trace(go.Bar(
            x=[data['Instrument'] for data in funding_data],
            y=[data['Annualized'] for data in funding_data],
            name='Annualized Funding Rate (%)',
            marker_color='blue'
        ))
        
        fig.update_layout(
            title="Annualized Funding Rates by Instrument",
            xaxis_title="Instrument", yaxis_title="Annualized Rate (%)",
            height=400, margin=dict(l=0, r=0, t=40, b=0)
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        st.subheader("Funding Rate Details")
        st.dataframe(funding_df, use_container_width=True)
    else:
        st.info("Loading funding rate data...")

# Recent Trades Tab
with tab3:
    st.header("Recent Large Trades")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        if 'ws_state' in st.session_state and st.session_state.ws_state.connected:
            status = st.session_state.ws_state.get_status()
            connection_type = status.get('connection_type', 'unknown')
            st.success(f"WebSocket Connected ({connection_type}) - Receiving Live Trade Data")
            st.info(f"Queue: {ws_message_queue.qsize()} | Trades: {len(st.session_state.trades)} | Threshold: ${trade_threshold:,}")
        else:
            st.warning("WebSocket Disconnected - Click 'Connect' to start")
    
    with col2:
        if st.button("Connect WebSocket", key="trades_connect"):
            result = start_websocket(use_alternative=False)
            st.info(result)
    
    with col3:
        if st.button("Try Alternative", key="trades_alt_connect"):
            result = start_websocket(use_alternative=True)
            st.info(result)
    
    if st.session_state.trades:
        st.info(f"Received {len(st.session_state.trades)} large trades (>${trade_threshold:,})")
        
        # Convert to DataFrame
        trades_df = pd.DataFrame(st.session_state.trades)
        trades_df = trades_df.sort_values('time', ascending=False)
        
        # Format for display
        trades_display = trades_df.copy()
        trades_display['time'] = trades_display['time'].dt.strftime('%H:%M:%S')
        trades_display['notional'] = trades_display['notional'].apply(lambda x: f"${x:,.2f}")
        trades_display['color'] = trades_display['side'].apply(lambda x: "🟢" if x == "Buy" else "🔴")
        trades_display['trade'] = trades_display['color'] + " " + trades_display['side']
        
        st.subheader(f"Recent Large Trades (>${trade_threshold:,})")
        st.dataframe(
            trades_display[['time', 'instrument', 'trade', 'price', 'size', 'notional']],
            use_container_width=True
        )
        
        if len(trades_df) > 3:
            # Trade volume visualization
            st.subheader("Trade Volume by Instrument and Side")
            
            trade_summary = trades_df.groupby(['instrument', 'side'])['notional'].sum().reset_index()
            
            fig = go.Figure()
            
            for instrument in instruments:
                instrument_data = trade_summary[trade_summary['instrument'] == instrument]
                
                buy_data = instrument_data[instrument_data['side'] == 'Buy']
                buy_volume = buy_data['notional'].sum() if not buy_data.empty else 0
                
                sell_data = instrument_data[instrument_data['side'] == 'Sell']
                sell_volume = sell_data['notional'].sum() if not sell_data.empty else 0
                
                fig.add_trace(go.Bar(
                    x=[instrument], y=[buy_volume],
                    name=f'{instrument} Buy', marker_color='green'
                ))
                
                fig.add_trace(go.Bar(
                    x=[instrument], y=[sell_volume],
                    name=f'{instrument} Sell', marker_color='red'
                ))
            
            fig.update_layout(
                title="Large Trade Volume by Instrument and Side",
                xaxis_title="Instrument", yaxis_title="Volume ($)",
                barmode='group', height=400
            )
            
            st.plotly_chart(fig, use_container_width=True)
            
            # Trade statistics
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
        st.info(f"Waiting for trades data... Connect WebSocket to see real-time trades above ${trade_threshold:,}")

# Debug Tab
with tab4:
    st.header("Debug Information")
    
    # Connection Status
    st.subheader("Connection Status")
    
    if 'ws_state' in st.session_state:
        status = st.session_state.ws_state.get_status()
        
        status_cols = st.columns(5)
        
        with status_cols[0]:
            if status['connected']:
                st.success("Connected")
            else:
                st.error("Disconnected")
        
        with status_cols[1]:
            if status['thread_alive']:
                st.success("Thread Alive")
            else:
                st.error("Thread Dead")
        
        with status_cols[2]:
            queue_size = ws_message_queue.qsize()
            if queue_size > 0:
                st.success(f"Queue: {queue_size}")
            else:
                st.warning("Queue: Empty")
        
        with status_cols[3]:
            if status['time_since_message'] < 60:
                st.success(f"Last Msg: {status['time_since_message']:.1f}s")
            else:
                st.error(f"Last Msg: {status['time_since_message']:.1f}s")
        
        with status_cols[4]:
            connection_type = status.get('connection_type', 'unknown')
            st.info(f"Type: {connection_type}")
        
        # Detailed status
        st.subheader("Detailed Status")
        col1, col2 = st.columns(2)
        
        with col1:
            st.write(f"**Session ID:** {status['session_id']}")
            st.write(f"**Reconnect Attempts:** {status['reconnect_attempt']}")
            st.write(f"**Should Run:** {status['should_run']}")
            st.write(f"**Subscriptions:** {status['subscriptions']}")
            st.write(f"**Connection Type:** {status.get('connection_type', 'unknown')}")
            
            if status['last_message_time'] > 0:
                last_msg_time = datetime.fromtimestamp(status['last_message_time']).strftime('%H:%M:%S')
                st.write(f"**Last Message Time:** {last_msg_time}")
            
            if status['time_since_ping'] < float('inf'):
                st.write(f"**Last Ping:** {status['time_since_ping']:.1f}s ago")
        
        with col2:
            # Control buttons
            if st.button("Standard WebSocket", key="force_reconnect"):
                result = start_websocket(use_alternative=False)
                st.success(result)
            
            if st.button("Simple Test", key="alt_reconnect"):
                result = start_websocket(use_alternative=True)
                st.success(result)
            
            if st.button("Manual Test", key="manual_test"):
                result = test_simple_websocket()
                st.success(result)
            
            if st.button("Stop Connection", key="stop_connection"):
                if 'ws_state' in st.session_state:
                    st.session_state.ws_state.stop()
                    if st.session_state.ws_state.ws_instance:
                        st.session_state.ws_state.ws_instance.close()
                st.success("Connection stopped")
            
            if st.button("Clear All Data", key="clear_data"):
                st.session_state.trades = []
                st.session_state.order_books = {}
                st.session_state.latest_prices = {}
                st.success("Data cleared")
    
    # Debug Messages
    st.subheader("Debug Messages")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        if st.session_state.debug_msgs:
            # Show latest messages first
            recent_msgs = st.session_state.debug_msgs[-50:]
            st.code('\n'.join(reversed(recent_msgs)))
        else:
            st.info("No debug messages yet")
    
    with col2:
        if st.button("Refresh Debug", key="refresh_debug"):
            st.rerun()
        
        if st.button("Clear Debug", key="clear_debug"):
            st.session_state.debug_msgs = []
            st.success("Debug messages cleared")
    
    # Raw Data
    st.subheader("Raw Trade Data")
    if st.session_state.trades:
        st.write(f"**Total trades:** {len(st.session_state.trades)}")
        if st.session_state.trades:
            st.json(st.session_state.trades[-3:])
    else:
        st.info("No trade data available")
    
    # Latest Prices
    st.subheader("Latest Prices")
    if st.session_state.latest_prices:
        prices_df = pd.DataFrame(list(st.session_state.latest_prices.items()), 
                                columns=['Instrument', 'Price'])
        st.dataframe(prices_df, use_container_width=True)
    else:
        st.info("No price data available")

# Main processing loop
# Process debug messages
debug_count = process_debug_messages()

# Process WebSocket messages
result, processed_count = process_websocket_messages()

# Update statistics
if 'processed_stats' not in st.session_state:
    st.session_state.processed_stats = []

st.session_state.processed_stats.append({
    'time': datetime.now(),
    'count': processed_count
})

# Keep only recent stats
if len(st.session_state.processed_stats) > 100:
    st.session_state.processed_stats = st.session_state.processed_stats[-50:]

# Display processing result in sidebar
if processed_count > 0:
    st.sidebar.text(f"Processed: {processed_count} msgs")

# Auto-start WebSocket if not connected
if 'ws_state' not in st.session_state:
    st.session_state.ws_state = WebSocketState()

if not st.session_state.ws_state.connected:
    if not st.session_state.ws_state.thread or not st.session_state.ws_state.thread.is_alive():
        # Auto-start with standard WebSocket
        add_debug_msg("Auto-starting WebSocket connection")
        start_websocket(use_alternative=False)

# Initial data load
if not st.session_state.order_books or not st.session_state.funding_rates:
    update_data()

# Auto-refresh
if st.sidebar.checkbox("Auto-refresh", value=True, key="auto_refresh"):
    refresh_rate = st.sidebar.slider("Refresh rate (sec)", 1, 30, 5, key="refresh_rate")
    st.sidebar.write(f"Dashboard refreshes every {refresh_rate} seconds")
    
    # Show connection status
    if 'ws_state' in st.session_state and st.session_state.ws_state.connected:
        status = st.session_state.ws_state.get_status()
        connection_type = status.get('connection_type', 'unknown')
        st.sidebar.success(f"WebSocket OK ({connection_type}) | Queue: {ws_message_queue.qsize()}")
    else:
        st.sidebar.error("WebSocket disconnected")
    
    time.sleep(refresh_rate)
    st.rerun()
