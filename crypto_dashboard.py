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
from pathlib import Path

# Add the parent directory to the path so we can import hyperliquid
sys.path.insert(0, str(Path(__file__).resolve().parent))

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

# Import hyperliquid after adding to path
try:
    from hyperliquid.info import Info
    from hyperliquid.utils.types import Subscription
except ImportError as e:
    st.error(f"Could not import Hyperliquid. Error: {str(e)}")
    st.error("Make sure the hyperliquid package is available in the path.")
    st.stop()

# Global queues for thread-safe communication
ws_message_queue = queue.Queue(maxsize=1000)
debug_message_queue = queue.Queue(maxsize=500)

class DashboardState:
    """Centralized state management for the dashboard"""
    def __init__(self):
        self.ws_connected = False
        self.last_message_time = 0
        self.connection_thread = None
        self.info_client = None
        self.session_id = None
        self.should_run = True
        self._lock = threading.Lock()
    
    def mark_connected(self):
        with self._lock:
            self.ws_connected = True
            self.last_message_time = time.time()
    
    def mark_disconnected(self):
        with self._lock:
            self.ws_connected = False
    
    def update_message_time(self):
        with self._lock:
            self.last_message_time = time.time()
    
    def get_status(self):
        with self._lock:
            return {
                'connected': self.ws_connected,
                'last_message': self.last_message_time,
                'time_since_message': time.time() - self.last_message_time if self.last_message_time > 0 else float('inf'),
                'thread_alive': self.connection_thread is not None and self.connection_thread.is_alive(),
                'should_run': self.should_run
            }

def initialize_session_state():
    """Initialize all session state variables"""
    if 'dashboard_state' not in st.session_state:
        st.session_state.dashboard_state = DashboardState()
    
    if 'order_books' not in st.session_state:
        st.session_state.order_books = {}
    if 'funding_rates' not in st.session_state:
        st.session_state.funding_rates = {}
    if 'trades' not in st.session_state:
        st.session_state.trades = []
    if 'latest_prices' not in st.session_state:
        st.session_state.latest_prices = {}
    if 'debug_msgs' not in st.session_state:
        st.session_state.debug_msgs = []
    if 'last_update' not in st.session_state:
        st.session_state.last_update = datetime.now()

def add_debug_msg(msg):
    """Add debug message with timestamp"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    full_msg = f"{timestamp}: {msg}"
    
    try:
        debug_message_queue.put(full_msg, block=False)
    except queue.Full:
        # Remove old messages if queue is full
        try:
            for _ in range(10):
                debug_message_queue.get_nowait()
            debug_message_queue.put(full_msg, block=False)
        except:
            pass
    
    logger.info(full_msg)

def create_info_client():
    """Create Hyperliquid Info client"""
    return Info(skip_ws=False)  # Enable WebSocket for real-time data

def websocket_message_handler(ws_msg):
    """Handle incoming WebSocket messages"""
    try:
        # Update connection status
        st.session_state.dashboard_state.update_message_time()
        
        # Queue message for processing
        ws_message_queue.put(ws_msg, block=False)
        
    except queue.Full:
        # Clear old messages if queue is full
        try:
            for _ in range(50):
                ws_message_queue.get_nowait()
            ws_message_queue.put(ws_msg, block=False)
        except:
            pass
    except Exception as e:
        add_debug_msg(f"Error in message handler: {str(e)}")

def start_websocket_connection(instruments):
    """Start WebSocket connection using Hyperliquid's built-in WebSocket"""
    add_debug_msg("Starting Hyperliquid WebSocket connection")
    
    try:
        # Create Info client with WebSocket enabled
        info_client = Info(skip_ws=False)
        st.session_state.dashboard_state.info_client = info_client
        
        add_debug_msg("Info client created successfully")
        
        # Subscribe to all mids
        try:
            allmids_subscription: Subscription = {"type": "allMids"}
            info_client.subscribe(allmids_subscription, websocket_message_handler)
            add_debug_msg("Subscribed to allMids")
        except Exception as e:
            add_debug_msg(f"Error subscribing to allMids: {str(e)}")
        
        # Subscribe to instrument-specific data
        for instrument in instruments:
            try:
                # Subscribe to L2 order book
                l2_subscription: Subscription = {"type": "l2Book", "coin": instrument}
                info_client.subscribe(l2_subscription, websocket_message_handler)
                add_debug_msg(f"Subscribed to L2 book for {instrument}")
                
                # Subscribe to trades
                trades_subscription: Subscription = {"type": "trades", "coin": instrument}
                info_client.subscribe(trades_subscription, websocket_message_handler)
                add_debug_msg(f"Subscribed to trades for {instrument}")
                
            except Exception as e:
                add_debug_msg(f"Error subscribing to {instrument}: {str(e)}")
        
        # Mark as connected
        st.session_state.dashboard_state.mark_connected()
        add_debug_msg("WebSocket subscriptions completed")
        
        return True
        
    except Exception as e:
        add_debug_msg(f"Error starting WebSocket connection: {str(e)}")
        st.session_state.dashboard_state.mark_disconnected()
        return False

def websocket_thread_function(instruments):
    """WebSocket thread function"""
    thread_id = threading.get_ident()
    add_debug_msg(f"WebSocket thread {thread_id} started")
    
    try:
        success = start_websocket_connection(instruments)
        if success:
            add_debug_msg("WebSocket connection established successfully")
            
            # Keep thread alive
            while st.session_state.dashboard_state.should_run:
                time.sleep(1)
        else:
            add_debug_msg("Failed to establish WebSocket connection")
            
    except Exception as e:
        add_debug_msg(f"WebSocket thread error: {str(e)}")
    finally:
        add_debug_msg(f"WebSocket thread {thread_id} ended")

def start_websocket_thread(instruments):
    """Start WebSocket in a background thread"""
    if st.session_state.dashboard_state.connection_thread and st.session_state.dashboard_state.connection_thread.is_alive():
        add_debug_msg("WebSocket thread already running")
        return "WebSocket already running"
    
    # Create and start thread
    thread = threading.Thread(target=websocket_thread_function, args=(instruments,), daemon=True)
    thread.start()
    st.session_state.dashboard_state.connection_thread = thread
    
    add_debug_msg("WebSocket thread started")
    return "WebSocket thread started"

def process_websocket_messages():
    """Process queued WebSocket messages"""
    processed_count = 0
    trade_count = 0
    orderbook_count = 0
    price_count = 0
    
    # Process up to 50 messages per cycle to avoid blocking UI
    max_process = min(50, ws_message_queue.qsize())
    
    while processed_count < max_process:
        try:
            ws_msg = ws_message_queue.get_nowait()
            processed_count += 1
            
            channel = ws_msg.get('channel', 'unknown')
            
            # Debug first few messages
            if processed_count <= 3:
                add_debug_msg(f"Processing {channel} message")
            
            # Process different message types
            if channel == 'allMids':
                # Update latest prices
                data = ws_msg.get('data', {})
                mids = data.get('mids', {})
                for coin, price_str in mids.items():
                    try:
                        st.session_state.latest_prices[coin] = float(price_str)
                        price_count += 1
                    except (ValueError, TypeError):
                        pass
            
            elif channel == 'l2Book':
                # Update order book
                data = ws_msg.get('data', {})
                coin = data.get('coin')
                if coin:
                    st.session_state.order_books[coin] = data
                    orderbook_count += 1
            
            elif channel == 'trades':
                # Process trades
                trades = ws_msg.get('data', [])
                for trade in trades:
                    try:
                        coin = trade.get('coin')
                        if not coin:
                            continue
                        
                        # Convert side code to readable format
                        side_code = trade.get('side')
                        side = "Sell" if side_code == "A" else "Buy" if side_code == "B" else side_code
                        
                        # Validate required fields
                        if 'sz' not in trade or 'px' not in trade:
                            continue
                        
                        size = float(trade['sz'])
                        price = float(trade['px'])
                        notional = size * price
                        
                        # Only record large trades (>$1000)
                        if notional > 1000:
                            trade_time = datetime.fromtimestamp(
                                int(trade.get('time', time.time() * 1000)) / 1000
                            )
                            
                            # Limit trades list size
                            if len(st.session_state.trades) >= 100:
                                st.session_state.trades = st.session_state.trades[-50:]
                            
                            st.session_state.trades.append({
                                'instrument': coin,
                                'side': side,
                                'size': size,
                                'price': price,
                                'notional': notional,
                                'time': trade_time
                            })
                            
                            trade_count += 1
                            
                    except (ValueError, TypeError, KeyError) as e:
                        continue
            
        except queue.Empty:
            break
        except Exception as e:
            add_debug_msg(f"Error processing message: {str(e)}")
    
    # Create summary
    if processed_count > 0:
        summary = (f"Processed {processed_count} messages: "
                   f"{price_count} prices, {orderbook_count} books, {trade_count} trades")
        if processed_count > 5:
            add_debug_msg(summary)
    else:
        summary = f"No messages (queue: {ws_message_queue.qsize()})"
    
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
        st.session_state.debug_msgs.extend(new_messages)
        if len(st.session_state.debug_msgs) > 100:
            st.session_state.debug_msgs = st.session_state.debug_msgs[-50:]
    
    return len(new_messages)

# REST API functions for fallback data
@st.cache_data(ttl=30)
def get_order_book_fallback(instrument):
    """Get order book using REST API as fallback"""
    try:
        info_client = Info(skip_ws=True)
        return info_client.l2_snapshot(instrument)
    except Exception as e:
        st.error(f"Error fetching order book for {instrument}: {str(e)}")
        return None

@st.cache_data(ttl=60)
def get_funding_rate_fallback(instrument):
    """Get funding rate using REST API as fallback"""
    try:
        info_client = Info(skip_ws=True)
        now = int(time.time() * 1000)
        start_time = now - (2 * 60 * 60 * 1000)  # 2 hours ago
        
        funding_history = info_client.funding_history(instrument, start_time, now)
        
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

def update_fallback_data(instruments):
    """Update data using REST API fallback"""
    for instrument in instruments:
        # Update order book
        order_book = get_order_book_fallback(instrument)
        if order_book:
            st.session_state.order_books[instrument] = order_book
        
        # Update funding rate
        funding_rate = get_funding_rate_fallback(instrument)
        if funding_rate:
            st.session_state.funding_rates[instrument] = funding_rate
    
    st.session_state.last_update = datetime.now()

# Initialize session state
initialize_session_state()

# Sidebar configuration
st.sidebar.title("Crypto Arbitrage Dashboard")

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

trade_threshold = st.sidebar.slider(
    "Large Trade Threshold ($)",
    min_value=100,
    max_value=50000,
    value=1000,
    step=100
)

# WebSocket status in sidebar
status = st.session_state.dashboard_state.get_status()

if status['connected']:
    st.sidebar.success("🟢 WebSocket Connected")
    st.sidebar.text(f"Last message: {status['time_since_message']:.1f}s ago")
    st.sidebar.text(f"Queue: {ws_message_queue.qsize()}")
else:
    st.sidebar.error("🔴 WebSocket Disconnected")
    if st.sidebar.button("Connect WebSocket"):
        result = start_websocket_thread(instruments)
        st.sidebar.info(result)

# Main title
st.title("🚀 Crypto Market Dashboard")
st.markdown("Real-time monitoring of order books, funding rates, and large trades")

# Create tabs
tab1, tab2, tab3, tab4 = st.tabs(["📊 Order Books", "💰 Funding Rates", "📈 Live Trades", "🐛 Debug"])

# Order Books Tab
with tab1:
    st.header("Order Book Data")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        st.text(f"Last updated: {st.session_state.last_update.strftime('%H:%M:%S')}")
    with col2:
        if st.button("🔄 Refresh", key="refresh_books"):
            update_fallback_data(instruments)
    
    # Display order books
    if len(instruments) > 0:
        cols = st.columns(len(instruments))
        
        for i, instrument in enumerate(instruments):
            with cols[i]:
                # Use real-time data if available, otherwise fallback
                order_book = st.session_state.order_books.get(instrument)
                if not order_book:
                    order_book = get_order_book_fallback(instrument)
                
                if order_book and 'levels' in order_book and len(order_book['levels']) >= 2:
                    bids = order_book['levels'][0][:10]
                    asks = order_book['levels'][1][:10]
                    
                    if bids and asks:
                        # Calculate metrics
                        top_bid = float(bids[0]['px'])
                        top_ask = float(asks[0]['px'])
                        mid_price = (top_bid + top_ask) / 2
                        spread = top_ask - top_bid
                        spread_bps = (spread / mid_price) * 10000
                        
                        # Header with metrics
                        st.markdown(f"""
                        <div style='background: linear-gradient(90deg, #1f2937, #374151); 
                                    padding: 12px; border-radius: 8px; margin-bottom: 10px;'>
                            <h3 style='color: #f3f4f6; margin: 0; text-align: center;'>{instrument}</h3>
                            <div style='display: flex; justify-content: space-between; margin-top: 8px; font-size: 0.9em;'>
                                <span style='color: #22c55e;'>Bid: ${top_bid:,.2f}</span>
                                <span style='color: #ef4444;'>Ask: ${top_ask:,.2f}</span>
                                <span style='color: #f59e0b;'>{spread_bps:.1f}bps</span>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # Order book tables
                        col_ask, col_bid = st.columns(2)
                        
                        with col_ask:
                            st.markdown("**🔴 Asks**")
                            ask_df = pd.DataFrame(asks)
                            ask_df['px'] = pd.to_numeric(ask_df['px'])
                            ask_df['sz'] = pd.to_numeric(ask_df['sz'])
                            ask_df = ask_df.sort_values('px', ascending=True)
                            
                            display_asks = ask_df[['px', 'sz']].head(5)
                            display_asks.columns = ['Price', 'Size']
                            st.dataframe(
                                display_asks.style.background_gradient(
                                    subset=['Size'], cmap='Reds', vmin=0
                                ).format({'Price': '${:.2f}', 'Size': '{:.4f}'}),
                                use_container_width=True,
                                height=200
                            )
                        
                        with col_bid:
                            st.markdown("**🟢 Bids**")
                            bid_df = pd.DataFrame(bids)
                            bid_df['px'] = pd.to_numeric(bid_df['px'])
                            bid_df['sz'] = pd.to_numeric(bid_df['sz'])
                            bid_df = bid_df.sort_values('px', ascending=False)
                            
                            display_bids = bid_df[['px', 'sz']].head(5)
                            display_bids.columns = ['Price', 'Size']
                            st.dataframe(
                                display_bids.style.background_gradient(
                                    subset=['Size'], cmap='Greens', vmin=0
                                ).format({'Price': '${:.2f}', 'Size': '{:.4f}'}),
                                use_container_width=True,
                                height=200
                            )
                        
                        # Order book depth chart
                        fig = go.Figure()
                        
                        # Add bids (green)
                        fig.add_trace(go.Bar(
                            x=bid_df['px'].head(10),
                            y=bid_df['sz'].head(10),
                            name='Bids',
                            marker_color='rgba(34, 197, 94, 0.7)',
                            showlegend=False
                        ))
                        
                        # Add asks (red)
                        fig.add_trace(go.Bar(
                            x=ask_df['px'].head(10),
                            y=ask_df['sz'].head(10),
                            name='Asks',
                            marker_color='rgba(239, 68, 68, 0.7)',
                            showlegend=False
                        ))
                        
                        # Add mid price line
                        fig.add_vline(
                            x=mid_price,
                            line=dict(color='#f59e0b', width=2, dash='dash'),
                            annotation_text=f"Mid: ${mid_price:,.2f}"
                        )
                        
                        fig.update_layout(
                            title=f"{instrument} Order Book Depth",
                            xaxis_title="Price",
                            yaxis_title="Size",
                            height=300,
                            template="plotly_dark",
                            margin=dict(l=0, r=0, t=30, b=0)
                        )
                        
                        st.plotly_chart(fig, use_container_width=True)
                        
                else:
                    st.warning(f"No order book data for {instrument}")

# Funding Rates Tab
with tab2:
    st.header("Funding Rate Analysis")
    
    if st.button("🔄 Refresh Funding", key="refresh_funding"):
        update_fallback_data(instruments)
    
    funding_data = []
    for instrument in instruments:
        funding_rate = st.session_state.funding_rates.get(instrument)
        if not funding_rate:
            funding_rate = get_funding_rate_fallback(instrument)
        
        if funding_rate:
            funding_data.append({
                'Instrument': instrument,
                'Rate (%)': funding_rate['rate'] * 100,
                'Annualized (%)': funding_rate['rate'] * 3 * 365 * 100,
                'Next Funding': datetime.fromtimestamp(funding_rate['next_funding_time']),
                'Time to Next (h)': (datetime.fromtimestamp(funding_rate['next_funding_time']) - datetime.now()).total_seconds() / 3600
            })
    
    if funding_data:
        # Metrics row
        st.subheader("Current Funding Rates")
        metrics_cols = st.columns(len(funding_data))
        
        for i, data in enumerate(funding_data):
            with metrics_cols[i]:
                color = "normal"
                if abs(data['Annualized (%)']) > 50:
                    color = "inverse"
                elif abs(data['Annualized (%)']) > 20:
                    color = "off"
                
                st.metric(
                    data['Instrument'],
                    f"{data['Rate (%)']:.4f}%",
                    f"{data['Annualized (%)']:.1f}% ann."
                )
                st.caption(f"Next: {data['Next Funding'].strftime('%H:%M')} ({data['Time to Next (h)']:.1f}h)")
        
        # Funding rate comparison chart
        st.subheader("Annualized Funding Rate Comparison")
        
        fig = go.Figure()
        
        colors = ['#22c55e' if rate > 0 else '#ef4444' for rate in [d['Annualized (%)'] for d in funding_data]]
        
        fig.add_trace(go.Bar(
            x=[d['Instrument'] for d in funding_data],
            y=[d['Annualized (%)'] for d in funding_data],
            marker_color=colors,
            text=[f"{d['Annualized (%)']:.1f}%" for d in funding_data],
            textposition='auto',
        ))
        
        fig.update_layout(
            title="Annualized Funding Rates",
            xaxis_title="Instrument",
            yaxis_title="Annualized Rate (%)",
            template="plotly_dark",
            height=400,
            showlegend=False
        )
        
        # Add horizontal lines for reference
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        fig.add_hline(y=20, line_dash="dot", line_color="orange", annotation_text="20% threshold")
        fig.add_hline(y=-20, line_dash="dot", line_color="orange")
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Detailed table
        st.subheader("Detailed Funding Information")
        df = pd.DataFrame(funding_data)
        st.dataframe(
            df.style.format({
                'Rate (%)': '{:.6f}',
                'Annualized (%)': '{:.2f}',
                'Time to Next (h)': '{:.1f}'
            }),
            use_container_width=True
        )
    else:
        st.info("Loading funding rate data...")

# Live Trades Tab
with tab3:
    st.header("Live Trade Feed")
    
    # Connection status
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        if status['connected']:
            st.success(f"🟢 Live data connected | Queue: {ws_message_queue.qsize()}")
            st.info(f"Showing trades > ${trade_threshold:,} | Total recorded: {len(st.session_state.trades)}")
        else:
            st.warning("🔴 Not connected to live feed")
    
    with col2:
        if st.button("🔌 Connect", key="connect_trades"):
            result = start_websocket_thread(instruments)
            st.success(result)
    
    with col3:
        if st.button("🗑️ Clear Trades", key="clear_trades"):
            st.session_state.trades = []
            st.success("Trades cleared")
    
    if st.session_state.trades:
        # Recent trades table
        trades_df = pd.DataFrame(st.session_state.trades)
        trades_df = trades_df.sort_values('time', ascending=False)
        
        # Format display
        display_df = trades_df.head(20).copy()
        display_df['time'] = display_df['time'].dt.strftime('%H:%M:%S')
        display_df['side_color'] = display_df['side'].apply(lambda x: "🟢" if x == "Buy" else "🔴")
        display_df['trade'] = display_df['side_color'] + " " + display_df['side']
        display_df['notional'] = display_df['notional'].apply(lambda x: f"${x:,.0f}")
        display_df['price'] = display_df['price'].apply(lambda x: f"${x:,.2f}")
        
        st.subheader("Recent Large Trades")
        st.dataframe(
            display_df[['time', 'instrument', 'trade', 'price', 'size', 'notional']].rename(columns={
                'time': 'Time',
                'instrument': 'Asset',
                'trade': 'Side',
                'price': 'Price',
                'size': 'Size',
                'notional': 'Value'
            }),
            use_container_width=True,
            height=400
        )
        
        if len(trades_df) > 5:
            # Trade analysis
            col1, col2 = st.columns(2)
            
            with col1:
                # Volume by instrument
                volume_by_instrument = trades_df.groupby('instrument')['notional'].sum().reset_index()
                volume_by_instrument = volume_by_instrument.sort_values('notional', ascending=False)
                
                fig = go.Figure(data=[
                    go.Pie(
                        labels=volume_by_instrument['instrument'],
                        values=volume_by_instrument['notional'],
                        hole=.3,
                        title="Trade Volume by Asset"
                    )
                ])
                fig.update_layout(template="plotly_dark", height=300)
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                # Buy vs Sell volume
                side_volume = trades_df.groupby('side')['notional'].sum().reset_index()
                
                fig = go.Figure(data=[
                    go.Pie(
                        labels=side_volume['side'],
                        values=side_volume['notional'],
                        hole=.3,
                        marker_colors=['#22c55e', '#ef4444'],
                        title="Buy vs Sell Volume"
                    )
                ])
                fig.update_layout(template="plotly_dark", height=300)
                st.plotly_chart(fig, use_container_width=True)
            
            # Trade timeline
            st.subheader("Trade Timeline")
            
            # Group trades by 5-minute intervals
            trades_df['time_bucket'] = trades_df['time'].dt.floor('5T')
            timeline_data = trades_df.groupby(['time_bucket', 'side'])['notional'].sum().reset_index()
            
            fig = go.Figure()
            
            for side in ['Buy', 'Sell']:
                side_data = timeline_data[timeline_data['side'] == side]
                fig.add_trace(go.Bar(
                    x=side_data['time_bucket'],
                    y=side_data['notional'],
                    name=side,
                    marker_color='#22c55e' if side == 'Buy' else '#ef4444'
                ))
            
            fig.update_layout(
                title="Trade Volume Over Time (5-minute intervals)",
                xaxis_title="Time",
                yaxis_title="Volume ($)",
                template="plotly_dark",
                barmode='group',
                height=400
            )
            
            st.plotly_chart(fig, use_container_width=True)
    
    else:
        st.info(f"Waiting for trade data... Connect to WebSocket to see trades above ${trade_threshold:,}")

# Debug Tab
with tab4:
    st.header("Debug Information")
    
    # Connection status details
    st.subheader("Connection Status")
    
    status = st.session_state.dashboard_state.get_status()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        if status['connected']:
            st.success("🟢 Connected")
        else:
            st.error("🔴 Disconnected")
    
    with col2:
        if status['thread_alive']:
            st.success("🟢 Thread Active")
        else:
            st.error("🔴 Thread Inactive")
    
    with col3:
        queue_size = ws_message_queue.qsize()
        if queue_size > 0:
            st.success(f"📥 Queue: {queue_size}")
        else:
            st.warning("📥 Queue: Empty")
    
    with col4:
        if status['time_since_message'] < 30:
            st.success(f"⏰ Last: {status['time_since_message']:.1f}s")
        else:
            st.error(f"⏰ Last: {status['time_since_message']:.1f}s")
    
    # Control buttons
    st.subheader("Controls")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        if st.button("🔌 Connect WebSocket", key="debug_connect"):
            result = start_websocket_thread(instruments)
            st.success(result)
    
    with col2:
        if st.button("🔄 Refresh Data", key="debug_refresh"):
            update_fallback_data(instruments)
            st.success("Data refreshed")
    
    with col3:
        if st.button("🗑️ Clear Debug Log", key="clear_debug"):
            st.session_state.debug_msgs = []
            st.success("Debug log cleared")
    
    with col4:
        if st.button("🗑️ Clear All Data", key="clear_all_data"):
            st.session_state.trades = []
            st.session_state.order_books = {}
            st.session_state.latest_prices = {}
            st.success("All data cleared")
    
    # Debug messages
    st.subheader("Debug Messages")
    
    if st.session_state.debug_msgs:
        # Show latest 30 messages
        recent_msgs = st.session_state.debug_msgs[-30:]
        debug_text = '\n'.join(reversed(recent_msgs))
        st.text_area("Recent Debug Messages", debug_text, height=300)
    else:
        st.info("No debug messages yet")
    
    # System information
    st.subheader("System Information")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**WebSocket Queue Status:**")
        st.write(f"- Queue Size: {ws_message_queue.qsize()}")
        st.write(f"- Debug Queue: {debug_message_queue.qsize()}")
        st.write(f"- Instruments: {len(instruments)}")
        st.write(f"- Connected: {status['connected']}")
        
    with col2:
        st.write("**Data Status:**")
        st.write(f"- Order Books: {len(st.session_state.order_books)}")
        st.write(f"- Latest Prices: {len(st.session_state.latest_prices)}")
        st.write(f"- Total Trades: {len(st.session_state.trades)}")
        st.write(f"- Last Update: {st.session_state.last_update.strftime('%H:%M:%S')}")
    
    # Raw data preview
    if st.checkbox("Show Raw Data", key="show_raw_data"):
        st.subheader("Raw Data Preview")
        
        if st.session_state.latest_prices:
            st.write("**Latest Prices:**")
            st.json(dict(list(st.session_state.latest_prices.items())[:5]))
        
        if st.session_state.trades:
            st.write("**Recent Trades (Last 3):**")
            recent_trades = st.session_state.trades[-3:]
            trades_for_display = []
            for trade in recent_trades:
                trade_copy = trade.copy()
                trade_copy['time'] = trade_copy['time'].isoformat()
                trades_for_display.append(trade_copy)
            st.json(trades_for_display)

# Process messages and update debug info
process_debug_messages()
result, processed_count = process_websocket_messages()

# Auto-start WebSocket if not connected and no thread running
if not status['connected'] and not status['thread_alive']:
    if st.sidebar.button("🚀 Auto-Start WebSocket"):
        add_debug_msg("Auto-starting WebSocket connection")
        start_websocket_thread(instruments)

# Sidebar status update
if status['connected']:
    st.sidebar.success(f"🟢 Connected | Processed: {processed_count}")
    st.sidebar.text(f"Queue: {ws_message_queue.qsize()} | Trades: {len(st.session_state.trades)}")
else:
    st.sidebar.error("🔴 Disconnected")

# Initial data load if needed
if not st.session_state.order_books and not st.session_state.funding_rates:
    with st.spinner("Loading initial data..."):
        update_fallback_data(instruments)

# Auto-refresh functionality
if st.sidebar.checkbox("Auto-refresh", value=True):
    refresh_rate = st.sidebar.slider("Refresh rate (sec)", 1, 30, 5)
    st.sidebar.text(f"Auto-refreshing every {refresh_rate}s")
    
    # Show connection health
    if status['connected'] and status['time_since_message'] < 60:
        st.sidebar.success("📡 Live data streaming")
    elif ws_message_queue.qsize() > 0:
        st.sidebar.warning("📦 Processing queued data")
    else:
        st.sidebar.error("⚠️ No live data")
    
    time.sleep(refresh_rate)
    st.rerun()
