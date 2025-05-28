# Add trade count on secondary y-axis
                trade_count_over_time = trades_df_sorted.reset_index().index + 1
                fig.add_trace(
                    go.Scatter(x=trades_df_sorted['time'], y=trade_count_over_time,
                              mode='lines', name='Trade Count', line=dict(color='orange', dash='dash'), 
                              yaxis='y2', showlegend=False),
                    row=2, col=1, secondary_y=True
                )
            
            # Average trade size by instrument
            avg_sizes = trades_df.groupby('instrument')['notional'].mean().sort_values(ascending=False)
            fig.add_trace(
                go.Bar(x=avg_sizes.index, y=avg_sizes.values, 
                       marker_color='purple', opacity=0.8, showlegend=False),
                row=2, col=2
            )
            
            # Update layout
            fig.update_layout(
                height=800,
                title_text="Trade Analysis Dashboard",
                template="plotly_white"
            )
            
            # Update axes labels
            fig.update_xaxes(title_text="Instrument", row=1, col=1)
            fig.update_yaxes(title_text="Volume ($)", row=1, col=1)
            fig.update_xaxes(title_text="Time", row=2, col=1)
            fig.update_yaxes(title_text="Cumulative Volume ($)", row=2, col=1)
            fig.update_yaxes(title_text="Trade Count", secondary_y=True, row=2, col=1)
            fig.update_xaxes(title_text="Instrument", row=2, col=2)
            fig.update_yaxes(title_text="Average Size ($)", row=2, col=2)
            
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(f"🔍 Waiting for large trades (>${trade_threshold:,}+)")
        st.markdown("""
        **To see trades:**
        1. Make sure WebSocket is connected (check sidebar)
        2. Select instruments to monitor
        3. Adjust trade threshold if needed
        4. Wait for trading activity
        """)

# Tab 2: Enhanced Order Books
with tab2:
    st.header("📈 Order Books")
    
    # Refresh controls
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.write("**Real-time order book data from WebSocket and REST API**")
    with col2:
        if st.button("🔄 Refresh All", help="Refresh order books via REST API"):
            st.cache_data.clear()
            st.rerun()
    with col3:
        book_depth = st.selectbox("Depth:", [5, 10, 20], index=1, help="Number of price levels to show")
    
    if not instruments:
        st.warning("Please select instruments to monitor in the sidebar.")
    else:
        # Display order books in a grid
        cols_per_row = 2
        for i in range(0, len(instruments), cols_per_row):
            cols = st.columns(cols_per_row)
            
            for j, instrument in enumerate(instruments[i:i+cols_per_row]):
                with cols[j]:
                    st.subheader(f"📊 {instrument}")
                    
                    # Try WebSocket data first, then REST API
                    order_book = st.session_state.order_books.get(instrument)
                    data_source = "WebSocket"
                    
                    if not order_book:
                        order_book = get_order_book(instrument)
                        data_source = "REST API"
                    
                    if order_book and 'levels' in order_book and len(order_book['levels']) >= 2:
                        try:
                            bids = order_book['levels'][0][:book_depth]
                            asks = order_book['levels'][1][:book_depth]
                            
                            if not bids or not asks:
                                st.warning(f"Empty order book for {instrument}")
                                continue
                            
                            # Create dataframes with error handling
                            try:
                                bid_df = pd.DataFrame(bids)
                                bid_df['px'] = pd.to_numeric(bid_df['px'], errors='coerce')
                                bid_df['sz'] = pd.to_numeric(bid_df['sz'], errors='coerce')
                                bid_df = bid_df.dropna().sort_values('px', ascending=False)
                                
                                ask_df = pd.DataFrame(asks)
                                ask_df['px'] = pd.to_numeric(ask_df['px'], errors='coerce')
                                ask_df['sz'] = pd.to_numeric(ask_df['sz'], errors='coerce')
                                ask_df = ask_df.dropna().sort_values('px', ascending=True)
                                
                                if bid_df.empty or ask_df.empty:
                                    st.warning(f"Invalid price data for {instrument}")
                                    continue
                                
                                # Calculate metrics
                                top_bid = bid_df.iloc[0]['px']
                                top_ask = ask_df.iloc[0]['px']
                                mid_price = (top_bid + top_ask) / 2
                                spread = top_ask - top_bid
                                spread_bps = (spread / mid_price) * 10000
                                
                                # Display metrics in compact format
                                st.markdown(f"""
                                <div style='background: #f0f2f6; padding: 8px; border-radius: 4px; margin: 5px 0;'>
                                    <div style='display: flex; justify-content: space-between; font-size: 0.8em;'>
                                        <span><strong>Bid:</strong> ${top_bid:,.4f}</span>
                                        <span><strong>Ask:</strong> ${top_ask:,.4f}</span>
                                    </div>
                                    <div style='display: flex; justify-content: space-between; font-size: 0.8em;'>
                                        <span><strong>Mid:</strong> ${mid_price:,.4f}</span>
                                        <span><strong>Spread:</strong> {spread_bps:.2f}bps</span>
                                    </div>
                                    <div style='text-align: center; font-size: 0.7em; color: #666;'>
                                        Source: {data_source} | Updated: {datetime.now().strftime('%H:%M:%S')}
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)
                                
                                # Compact order book display
                                col_bid, col_ask = st.columns(2)
                                
                                with col_bid:
                                    st.markdown("**Bids**")
                                    bid_display = bid_df[['px', 'sz']].head(5).copy()
                                    bid_display.columns = ['Price', 'Size']
                                    bid_display['Price'] = bid_display['Price'].apply(lambda x: f"${x:.4f}")
                                    bid_display['Size'] = bid_display['Size'].apply(lambda x: f"{x:.3f}")
                                    st.dataframe(bid_display, use_container_width=True, height=200, hide_index=True)
                                
                                with col_ask:
                                    st.markdown("**Asks**")
                                    ask_display = ask_df[['px', 'sz']].head(5).copy()
                                    ask_display.columns = ['Price', 'Size']
                                    ask_display['Price'] = ask_display['Price'].apply(lambda x: f"${x:.4f}")
                                    ask_display['Size'] = ask_display['Size'].apply(lambda x: f"{x:.3f}")
                                    st.dataframe(ask_display, use_container_width=True, height=200, hide_index=True)
                                
                                # Mini depth chart
                                if len(bid_df) >= 3 and len(ask_df) >= 3:
                                    fig = go.Figure()
                                    
                                    # Add bids
                                    fig.add_trace(go.Bar(
                                        x=bid_df['px'].head(5),
                                        y=bid_df['sz'].head(5),
                                        name='Bids',
                                        marker_color='rgba(0, 255, 0, 0.6)',
                                        opacity=0.7
                                    ))
                                    
                                    # Add asks
                                    fig.add_trace(go.Bar(
                                        x=ask_df['px'].head(5),
                                        y=ask_df['sz'].head(5),
                                        name='Asks',
                                        marker_color='rgba(255, 0, 0, 0.6)',
                                        opacity=0.7
                                    ))
                                    
                                    fig.update_layout(
                                        height=200,
                                        margin=dict(l=0, r=0, t=20, b=0),
                                        showlegend=False,
                                        xaxis_title="Price",
                                        yaxis_title="Size",
                                        template="plotly_white"
                                    )
                                    
                                    st.plotly_chart(fig, use_container_width=True)
                                
                            except Exception as e:
                                st.error(f"Error processing order book data: {str(e)}")
                                
                        except Exception as e:
                            st.error(f"Error displaying order book: {str(e)}")
                    else:
                        st.warning(f"No order book data available for {instrument}")
                        st.info("Try refreshing or check WebSocket connection")

# Tab 3: Enhanced Funding Rates
with tab3:
    st.header("💰 Funding Rates")
    
    # Refresh controls
    col1, col2 = st.columns([3, 1])
    with col1:
        st.write("**Current funding rates and arbitrage opportunities**")
    with col2:
        if st.button("🔄 Refresh Rates"):
            st.cache_data.clear()
            st.rerun()
    
    if not instruments:
        st.warning("Please select instruments to monitor in the sidebar.")
    else:
        # Fetch funding data for all instruments
        funding_data = []
        funding_errors = []
        
        for instrument in instruments:
            funding = get_funding_rate(instrument)
            if funding:
                # Calculate additional metrics
                rate_pct = funding['rate'] * 100
                annualized_pct = funding['rate'] * 3 * 365 * 100  # 3 funding periods per day
                
                # Calculate time to next funding
                next_funding_dt = datetime.fromtimestamp(funding['next_funding_time'])
                time_to_next = (next_funding_dt - datetime.now()).total_seconds() / 3600
                
                funding_data.append({
                    'Instrument': instrument,
                    'Current Rate (%)': rate_pct,
                    'Annualized (%)': annualized_pct,
                    'Premium': funding['premium'],
                    'Next Funding': next_funding_dt,
                    'Hours to Next': time_to_next,
                    'Data Points': funding.get('history_count', 0)
                })
            else:
                funding_errors.append(instrument)
        
        if funding_data:
            funding_df = pd.DataFrame(funding_data)
            
            # Summary metrics
            st.subheader("📊 Funding Rate Summary")
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                avg_rate = funding_df['Annualized (%)'].mean()
                st.metric("Average Rate", f"{avg_rate:.2f}%")
            
            with col2:
                max_rate = funding_df['Annualized (%)'].max()
                max_instrument = funding_df.loc[funding_df['Annualized (%)'].idxmax(), 'Instrument']
                st.metric("Highest Rate", f"{max_rate:.2f}%", delta=max_instrument)
            
            with col3:
                min_rate = funding_df['Annualized (%)'].min()
                min_instrument = funding_df.loc[funding_df['Annualized (%)'].idxmin(), 'Instrument']
                st.metric("Lowest Rate", f"{min_rate:.2f}%", delta=min_instrument)
            
            with col4:
                rate_spread = max_rate - min_rate
                st.metric("Rate Spread", f"{rate_spread:.2f}%", help="Difference between highest and lowest rates")
            
            # Detailed funding table
            st.subheader("📋 Detailed Funding Rates")
            
            # Format the dataframe for display
            display_df = funding_df.copy()
            display_df['Current Rate (%)'] = display_df['Current Rate (%)'].apply(lambda x: f"{x:.6f}%")
            display_df['Annualized (%)'] = display_df['Annualized (%)'].apply(lambda x: f"{x:.2f}%")
            display_df['Premium'] = display_df['Premium'].apply(lambda x: f"{x:.6f}")
            display_df['Next Funding'] = display_df['Next Funding'].apply(lambda x: x.strftime('%H:%M:%S'))
            display_df['Hours to Next'] = display_df['Hours to Next'].apply(lambda x: f"{x:.1f}h")
            
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
            # Funding rate visualization
            st.subheader("📈 Funding Rate Comparison")
            
            fig = make_subplots(
                rows=1, cols=2,
                subplot_titles=('Annualized Funding Rates', 'Time to Next Funding'),
                specs=[[{"secondary_y": False}, {"type": "bar"}]]
            )
            
            # Annualized rates bar chart
            colors = ['red' if x < 0 else 'green' for x in funding_df['Annualized (%)']]
            
            fig.add_trace(
                go.Bar(
                    x=funding_df['Instrument'],
                    y=funding_df['Annualized (%)'],
                    marker_color=colors,
                    opacity=0.8,
                    name='Annualized Rate'
                ),
                row=1, col=1
            )
            
            # Time to next funding
            fig.add_trace(
                go.Bar(
                    x=funding_df['Instrument'],
                    y=funding_df['Hours to Next'],
                    marker_color='blue',
                    opacity=0.6,
                    name='Hours to Next'
                ),
                row=1, col=2
            )
            
            fig.update_layout(
                height=400,
                showlegend=False,
                template="plotly_white"
            )
            
            fig.update_xaxes(title_text="Instrument", row=1, col=1)
            fig.update_yaxes(title_text="Annualized Rate (%)", row=1, col=1)
            fig.update_xaxes(title_text="Instrument", row=1, col=2)
            fig.update_yaxes(title_text="Hours", row=1, col=2)
            
            st.plotly_chart(fig, use_container_width=True)
            
            # Arbitrage opportunities
            if len(funding_df) > 1:
                st.subheader("⚡ Potential Arbitrage Opportunities")
                
                max_row = funding_df.loc[funding_df['Annualized (%)'].idxmax()]
                min_row = funding_df.loc[funding_df['Annualized (%)'].idxmin()]
                
                rate_diff = max_row['Annualized (%)'] - min_row['Annualized (%)']
                
                if rate_diff > 1.0:  # More than 1% difference
                    st.success(f"""
                    **Arbitrage Opportunity Detected!**
                    
                    📈 **Long:** {min_row['Instrument']} (Lower funding rate: {min_row['Annualized (%)']:.2f}%)
                    
                    📉 **Short:** {max_row['Instrument']} (Higher funding rate: {max_row['Annualized (%)']:.2f}%)
                    
                    💰 **Potential Annual Return:** {rate_diff:.2f}%
                    """)
                else:
                    st.info(f"Current rate spread ({rate_diff:.2f}%) may not justify arbitrage costs.")
        
        # Display any errors
        if funding_errors:
            st.warning(f"Could not fetch funding rates for: {', '.join(funding_errors)}")

# Tab 4: Statistics and Analytics
with tab4:
    st.header("📋 Trading Statistics & Analytics")
    
    # WebSocket performance stats
    st.subheader("🔗 Connection Performance")
    
    ws_stats = st.session_state.ws_state
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Messages Received", f"{ws_stats.message_count:,}")
    
    with col2:
        st.metric("Trades Processed", f"{ws_stats.trade_count:,}")
    
    with col3:
        st.metric("Order Book Updates", f"{ws_stats.orderbook_count:,}")
    
    with col4:
        st.metric("Total Volume", f"${ws_stats.total_volume:,.0f}")
    
    # Trade statistics
    if st.session_state.trades:
        trades_df = pd.DataFrame(st.session_state.trades)
        
        st.subheader("📊 Trade Statistics")
        
        # Time-based analysis
        if 'time' in trades_df.columns:
            trades_df['hour'] = trades_df['time'].dt.hour
            trades_df['minute'] = trades_df['time'].dt.minute
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Hourly Trade Distribution**")
                hourly_trades = trades_df['hour'].value_counts().sort_index()
                
                fig = go.Figure()
                fig.add_trace(go.Bar(x=hourly_trades.index, y=hourly_trades.values, marker_color='skyblue'))
                fig.update_layout(
                    xaxis_title="Hour (UTC)",
                    yaxis_title="Trade Count",
                    height=300,
                    template="plotly_white"
                )
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                st.write("**Volume Distribution by Size**")
                
                # Create size buckets
                trades_df['size_bucket'] = pd.cut(
                    trades_df['notional'], 
                    bins=[0, 1000, 5000, 10000, 50000, float('inf')],
                    labels=['$0-1K', '$1K-5K', '$5K-10K', '$10K-50K', '$50K+']
                )
                
                size_dist = trades_df['size_bucket'].value_counts()
                
                fig = go.Figure()
                fig.add_trace(go.Pie(
                    labels=size_dist.index,
                    values=size_dist.values,
                    hole=0.3
                ))
                fig.update_layout(height=300)
                st.plotly_chart(fig, use_container_width=True)
        
        # Instrument performance
        st.subheader("🎯 Instrument Performance")
        
        instrument_stats = trades_df.groupby('instrument').agg({
            'notional': ['count', 'sum', 'mean', 'std'],
            'size': 'mean',
            'price': ['min', 'max', 'mean']
        }).round(2)
        
        # Flatten column names
        instrument_stats.columns = ['_'.join(col).strip() for col in instrument_stats.columns]
        instrument_stats = instrument_stats.reset_index()
        
        # Rename columns for display
        display_columns = {
            'instrument': 'Instrument',
            'notional_count': 'Trade Count',
            'notional_sum': 'Total Volume ($)',
            'notional_mean': 'Avg Trade Size ($)',
            'notional_std': 'Volume Std Dev',
            'size_mean': 'Avg Size',
            'price_min': 'Min Price ($)',
            'price_max': 'Max Price ($)',
            'price_mean': 'Avg Price ($)'
        }
        
        instrument_stats = instrument_stats.rename(columns=display_columns)
        
        # Format numeric columns
        numeric_cols = ['Total Volume ($)', 'Avg Trade Size ($)', 'Min Price ($)', 'Max Price ($)', 'Avg Price ($)']
        for col in numeric_cols:
            if col in instrument_stats.columns:
                instrument_stats[col] = instrument_stats[col].apply(lambda x: f"${x:,.2f}")
        
        st.dataframe(instrument_stats, use_container_width=True, hide_index=True)
        
        # Recent activity timeline
        st.subheader("⏱️ Recent Activity Timeline")
        
        if len(trades_df) > 1:
            recent_trades = trades_df.tail(20).copy()
            
            fig = go.Figure()
            
            for idx, trade in recent_trades.iterrows():
                color = 'green' if trade['side'] == 'Buy' else 'red'
                fig.add_trace(go.Scatter(
                    x=[trade['time']],
                    y=[trade['price']],
                    mode='markers',
                    marker=dict(
                        size=max(8, min(30, trade['notional'] / 1000)),  # Size based on notional
                        color=color,
                        opacity=0.7
                    ),
                    text=f"{trade['instrument']}: {trade['side']} ${trade['notional']:,.0f}",
                    hovertemplate="%{text}<extra></extra>",
                    name=trade['instrument'],
                    showlegend=False
                ))
            
            fig.update_layout(
                title="Recent Trades (bubble size = trade value)",
                xaxis_title="Time",
                yaxis_title="Price ($)",
                height=400,
                template="plotly_white"
            )
            
            st.plotly_chart(fig, use_container_width=True)
    
    else:
        st.info("No trade data available for statistics. Start trading to see analytics!")
    
    # System performance
    st.subheader("⚙️ System Performance")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        queue_size = st.session_state.message_queue.qsize()
        queue_color = "🟢" if queue_size < 100 else "🟡" if queue_size < 500 else "🔴"
        st.metric("Message Queue", f"{queue_color} {queue_size}")
    
    with col2:
        if ws_stats.last_message_time > 0:
            time_since_msg = time.time() - ws_stats.last_message_time
            msg_color = "🟢" if time_since_msg < 10 else "🟡" if time_since_msg < 30 else "🔴"
            st.metric("Last Message", f"{msg_color} {time_since_msg:.1f}s ago")
    
    with col3:
        connection_uptime = time.time() - ws_stats.connection_start_time if ws_stats.connection_start_time > 0 else 0
        st.metric("Uptime", f"{connection_uptime:.0f}s")

# Tab 5: Enhanced Debug Information
with tab5:
    st.header("🔧 Debug & Diagnostics")
    
    # System status overview
    st.subheader("🖥️ System Status")
    
    status_data = {
        'WebSocket Connected': '✅ Yes' if st.session_state.ws_state.connected else '❌ No',
        'Session ID': st.session_state.ws_state.session_id,
        'Messages Received': f"{st.session_state.ws_state.message_count:,}",
        'Queue Size': st.session_state.message_queue.qsize(),
        'Selected Instruments': ', '.join(instruments) if instruments else 'None',
        'Trade Threshold': f"${trade_threshold:,}",
        'Total Trades Stored': len(st.session_state.trades),
        'Order Books Cached': len(st.session_state.order_books),
        'Latest Prices': len(st.session_state.latest_prices)
    }
    
    status_df = pd.DataFrame(list(status_data.items()), columns=['Parameter', 'Value'])
    st.dataframe(status_df, use_container_width=True, hide_index=True)
    
    # Connection history
    if st.session_state.connection_history:
        st.subheader("📝 Connection History")
        
        history_df = pd.DataFrame(st.session_state.connection_history)
        history_df['time_str'] = history_df['time'].dt.strftime('%H:%M:%S')
        history_df['event_icon'] = history_df['event'].apply(lambda x: '🟢' if x == 'connect' else '🔴')
        
        display_history = history_df[['time_str', 'event_icon', 'event', 'message']].tail(10)
        display_history.columns = ['Time', 'Status', 'Event', 'Message']
        
        st.dataframe(display_history, use_container_width=True, hide_index=True)
    
    # Control buttons
    st.subheader("🎛️ Debug Controls")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        if st.button("🗑️ Clear All Data", type="secondary", help="Clear all cached data"):
            st.session_state.trades = []
            st.session_state.order_books = {}
            st.session_state.latest_prices = {}
            st.session_state.debug_messages = []
            st.session_state.trade_stats = {'by_instrument': {}, 'by_side': {}, 'hourly': {}}
            st.cache_data.clear()
            st.success("✅ All data cleared!")
            st.rerun()
    
    with col2:
        if st.button("🔄 Reset WebSocket", type="secondary", help="Force WebSocket reconnection"):
            if st.session_state.ws_state.ws_instance:
                st.session_state.ws_state.ws_instance.close()
            st.session_state.ws_state.reset()
            time.sleep(0.5)
            result = start_websocket_thread()
            st.info(result)
            st.rerun()
    
    with col3:
        if st.button("💾 Export Data", type="secondary", help="Download trade data as CSV"):
            if st.session_state.trades:
                trades_df = pd.DataFrame(st.session_state.trades)
                csv = trades_df.to_csv(index=False)
                st.download_button(
                    label="📥 Download CSV",
                    data=csv,
                    file_name=f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )
            else:
                st.warning("No trade data to export")
    
    with col4:
        if st.button("🧹 Clear Debug Log", type="secondary", help="Clear debug messages"):
            st.session_state.debug_messages = []
            st.success("✅ Debug log cleared!")
    
    # Debug messages with filtering
    st.subheader("📋 Debug Messages")
    
    if st.session_state.debug_messages:
        # Message filtering
        col1, col2 = st.columns([2, 1])
        
        with col1:
            log_levels = ['ALL', 'INFO', 'WARN', 'ERROR']
            selected_level = st.selectbox("Filter by level:", log_levels, help="Filter debug messages by severity")
        
        with col2:
            max_messages = st.slider("Max messages:", 10, 100, 50, help="Maximum number of recent messages to show")
        
        # Filter messages
        filtered_messages = st.session_state.debug_messages
        if selected_level != 'ALL':
            filtered_messages = [msg for msg in filtered_messages if f'[{selected_level}]' in msg]
        
        # Display recent messages
        recent_messages = filtered_messages[-max_messages:] if filtered_messages else []
        
        if recent_messages:
            # Create a scrollable text area
            messages_text = '\n'.join(reversed(recent_messages))  # Most recent first
            st.text_area(
                "Debug Log:",
                value=messages_text,
                height=300,
                help="Recent debug messages (newest first)"
            )
        else:
            st.info(f"No {selected_level.lower()} messages found")
    else:
        st.info("No debug messages yet. Connect WebSocket to see activity logs.")
    
    # Raw data inspection
    with st.expander("🔍 Raw Data Inspection", expanded=False):
        st.subheader("Latest Prices")
        if st.session_state.latest_prices:
            st.json(st.session_state.latest_prices)
        else:
            st.info("No price data available")
        
        st.subheader("Recent Trades (Raw)")
        if st.session_state.trades:
            # Convert datetime objects to strings for JSON display
            trades_for_json = []
            for trade in st.session_state.trades[-3:]:
                trade_copy = trade.copy()
                trade_copy['time'] = trade_copy['time'].isoformat()
                trades_for_json.append(trade_copy)
            st.json(trades_for_json)
        else:
            st.info("No trade data available")
        
        st.subheader("Order Book Sample")
        if st.session_state.order_books:
            sample_instrument = list(st.session_state.order_books.keys())[0]
            sample_book = st.session_state.order_books[sample_instrument]
            st.write(f"**{sample_instrument} Order Book:**")
            st.json(sample_book)
        else:
            st.info("No order book data available")

# Auto-refresh logic
if auto_refresh:
    # Add a small status indicator
    st.sidebar.markdown(f"🔄 Auto-refreshing every {refresh_rate}s")
    
    # Sleep and refresh
    time.sleep(refresh_rate)
    st.rerun()
else:
    st.sidebar.markdown("⏸️ Auto-refresh disabled")#!/usr/bin/env python
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

# Main Application Layout
st.title("🚀 Crypto Arbitrage Dashboard")
st.markdown("**Real-time monitoring of order books, funding rates, and large trades from Hyperliquid**")

# Enhanced Sidebar
st.sidebar.title("⚙️ Dashboard Controls")

# Instrument selection with better organization
st.sidebar.subheader("🎯 Instruments")
available_instruments = ["BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE", "UNI", "MATIC", "ARB", "SUI", "APT"]

instruments = st.sidebar.multiselect(
    "Select trading pairs to monitor:",
    available_instruments,
    default=["BTC", "ETH", "SOL"],
    help="Choose which cryptocurrency pairs to monitor for trades and order books"
)
st.session_state.selected_instruments = instruments

if not instruments:
    st.sidebar.warning("⚠️ Please select at least one instrument!")

# Trade filtering
st.sidebar.subheader("🎯 Trade Filtering")
trade_threshold = st.sidebar.select_slider(
    "Minimum trade size to display:",
    options=[100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000],
    value=1000,
    format_func=lambda x: f"${x:,}",
    help="Only show trades larger than this amount"
)
st.session_state.trade_threshold = trade_threshold

# Display settings
st.sidebar.subheader("🎨 Display Settings")
max_trades_display = st.sidebar.slider(
    "Max trades to display:",
    min_value=25,
    max_value=200,
    value=100,
    step=25,
    help="Maximum number of recent trades to show in the table"
)

auto_refresh = st.sidebar.checkbox("🔄 Auto-refresh", value=True, help="Automatically refresh the dashboard")
if auto_refresh:
    refresh_rate = st.sidebar.slider("Refresh rate (seconds):", 1, 30, 3)

# Connection status and controls
render_connection_status()

# Process WebSocket messages
processing_stats = {'processed_count': 0, 'trade_count': 0, 'summary': 'No processing yet'}
if st.session_state.ws_state.connected:
    processing_stats = process_websocket_messages()
    
    # Display processing stats in sidebar
    if processing_stats['processed_count'] > 0:
        st.sidebar.text(f"📊 {processing_stats['summary']}")

# Main content area with enhanced tabs
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Live Trades", 
    "📈 Order Books", 
    "💰 Funding Rates", 
    "📋 Statistics",
    "🔧 Debug"
])

# Tab 1: Enhanced Live Trades
with tab1:
    st.header("📊 Live Large Trades")
    
    # Trade metrics
    render_trade_metrics()
    
    # Main trades display
    if st.session_state.trades:
        trades_df = pd.DataFrame(st.session_state.trades)
        
        st.subheader(f"📈 Recent Trades (>${trade_threshold:,}+)")
        
        # Enhanced trade table
        render_enhanced_trade_table(trades_df, max_trades_display)
        
        # Trade volume visualization
        if len(trades_df) > 3:
            st.subheader("📊 Trade Volume Analysis")
            
            # Create subplots for multiple visualizations
            fig = make_subplots(
                rows=2, cols=2,
                subplot_titles=('Volume by Instrument & Side', 'Trade Count by Instrument', 
                               'Volume Over Time', 'Average Trade Size'),
                specs=[[{"secondary_y": False}, {"type": "pie"}],
                       [{"secondary_y": True}, {"secondary_y": False}]]
            )
            
            # Volume by instrument and side
            volume_by_instrument = trades_df.groupby(['instrument', 'side'])['notional'].sum().reset_index()
            
            for instrument in instruments:
                if instrument in trades_df['instrument'].values:
                    inst_data = volume_by_instrument[volume_by_instrument['instrument'] == instrument]
                    
                    buy_volume = inst_data[inst_data['side'] == 'Buy']['notional'].sum() if not inst_data[inst_data['side'] == 'Buy'].empty else 0
                    sell_volume = inst_data[inst_data['side'] == 'Sell']['notional'].sum() if not inst_data[inst_data['side'] == 'Sell'].empty else 0
                    
                    fig.add_trace(
                        go.Bar(x=[instrument], y=[buy_volume], name=f'{instrument} Buy', 
                               marker_color='green', opacity=0.8, showlegend=False),
                        row=1, col=1
                    )
                    fig.add_trace(
                        go.Bar(x=[instrument], y=[sell_volume], name=f'{instrument} Sell', 
                               marker_color='red', opacity=0.8, showlegend=False),
                        row=1, col=1
                    )
            
            # Trade count pie chart
            trade_counts = trades_df['instrument'].value_counts()
            fig.add_trace(
                go.Pie(labels=trade_counts.index, values=trade_counts.values, 
                       name="Trade Count", showlegend=False),
                row=1, col=2
            )
            
            # Volume over time (if we have timestamps)
            if 'time' in trades_df.columns:
                trades_df_sorted = trades_df.sort_values('time')
                trades_df_sorted['cumulative_volume'] = trades_df_sorted['notional'].cumsum()
                
                fig.add_trace(
                    go.Scatter(x=trades_df_sorted['time'], y=trades_df_sorted['cumulative_volume'],
                              mode='lines', name='Cumulative Volume', line=dict(color='blue'), showlegend=False),
                    row=2, col=1
                )
                
                # Add trade count on secondary y-axis
                trade_count_over_time = trades_df_sorted.reset_index().index + 1
                fig.add_trace(
                    go.Scatter
