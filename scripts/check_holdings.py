#!/usr/bin/env python3
"""
Simple script to check if airdrop recipients have sold their tokens.
Compares current token holdings to original airdrop amounts.
"""

import csv
import os
import sys
import time
from typing import List, Dict
from dotenv import load_dotenv
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey
from spl.token.instructions import get_associated_token_address


def load_airdrop_data(csv_file_path: str) -> List[Dict]:
    """Load successful airdrop data from CSV file."""
    recipients = []
    
    if not os.path.exists(csv_file_path):
        print(f"Error: CSV file not found: {csv_file_path}")
        return recipients
    
    with open(csv_file_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            recipients.append({
                'sol_wallet': row['sol_wallet'],
                'airdropped_tokens': float(row['sol_nema_tokens']),
                'status': row['status']
            })
    
    print(f"Loaded {len(recipients)} airdrop recipients")
    return recipients


def get_current_token_balance(rpc_client: Client, wallet_address: str, token_mint: Pubkey, token_decimals: int = 6) -> float:
    """Get current token balance for a wallet."""
    try:
        wallet_pubkey = Pubkey.from_string(wallet_address)
        token_account = get_associated_token_address(wallet_pubkey, token_mint)
        
        # First check if the token account exists
        account_info = rpc_client.get_account_info(token_account)
        if account_info.value is None:
            # Token account doesn't exist, balance is 0
            return 0.0
        
        # Get token account balance
        balance_resp = rpc_client.get_token_account_balance(token_account)
        if balance_resp.value:
            raw_balance = float(balance_resp.value.amount)
            return raw_balance / (10 ** token_decimals)
        else:
            return 0.0
            
    except Exception as e:
        print(f"Error getting balance for {wallet_address}: {str(e)}")
        return 0.0


def check_holdings(recipients: List[Dict], rpc_client: Client, token_mint: Pubkey) -> List[Dict]:
    """Check current holdings vs airdropped amounts for all recipients."""
    results = []
    
    print("Checking current token holdings...")
    
    for i, recipient in enumerate(recipients):
        print(f"Checking {i+1}/{len(recipients)}: {recipient['sol_wallet']}")
        
        current_balance = get_current_token_balance(
            rpc_client, 
            recipient['sol_wallet'], 
            token_mint
        )
        
        # Add small delay to avoid rate limiting
        if (i + 1) % 5 == 0:
            time.sleep(1.0)
        
        airdropped_amount = recipient['airdropped_tokens']
        retention_percentage = (current_balance / airdropped_amount * 100) if airdropped_amount > 0 else 0
        
        # Categorize holder
        if current_balance >= airdropped_amount * 0.95:  # Allow for small rounding errors
            category = "full_holder"
        elif current_balance > 0:
            category = "partial_holder"
        else:
            category = "sold_all"
        
        result = {
            'sol_wallet': recipient['sol_wallet'],
            'airdropped_tokens': airdropped_amount,
            'current_balance': current_balance,
            'retention_percentage': round(retention_percentage, 2),
            'category': category
        }
        results.append(result)
        
        # Print result immediately to avoid losing data
        print(f"  Result: {current_balance:,.2f} / {airdropped_amount:,.2f} tokens ({retention_percentage:.1f}%) - {category}")
        
        # Save partial results every 25 wallets
        if (i + 1) % 25 == 0:
            output_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports', f'holdings_partial_{i+1}.csv')
            save_partial_results(results, output_file)
            print(f"  Saved partial results to {output_file}")
    
    return results


def save_partial_results(results: List[Dict], output_file: str):
    """Save partial results to avoid data loss."""
    try:
        with open(output_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'sol_wallet', 
                'airdropped_tokens', 
                'current_balance', 
                'retention_percentage', 
                'category'
            ])
            
            for result in results:
                writer.writerow([
                    result['sol_wallet'],
                    result['airdropped_tokens'],
                    result['current_balance'],
                    result['retention_percentage'],
                    result['category']
                ])
    except Exception as e:
        print(f"Error saving partial results: {e}")


def generate_report(results: List[Dict], output_file: str):
    """Generate CSV report with holdings analysis."""
    # Write detailed results
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'sol_wallet', 
            'airdropped_tokens', 
            'current_balance', 
            'retention_percentage', 
            'category'
        ])
        
        for result in results:
            writer.writerow([
                result['sol_wallet'],
                result['airdropped_tokens'],
                result['current_balance'],
                result['retention_percentage'],
                result['category']
            ])
    
    # Print summary
    total_recipients = len(results)
    full_holders = len([r for r in results if r['category'] == 'full_holder'])
    partial_holders = len([r for r in results if r['category'] == 'partial_holder'])
    sold_all = len([r for r in results if r['category'] == 'sold_all'])
    
    total_airdropped = sum(r['airdropped_tokens'] for r in results)
    total_remaining = sum(r['current_balance'] for r in results)
    overall_retention = (total_remaining / total_airdropped * 100) if total_airdropped > 0 else 0
    
    print("\n" + "="*50)
    print("AIRDROP HOLDINGS ANALYSIS REPORT")
    print("="*50)
    print(f"Total Recipients: {total_recipients}")
    print(f"Full Holders (≥95%): {full_holders} ({full_holders/total_recipients*100:.1f}%)")
    print(f"Partial Holders: {partial_holders} ({partial_holders/total_recipients*100:.1f}%)")
    print(f"Sold All: {sold_all} ({sold_all/total_recipients*100:.1f}%)")
    print("-"*50)
    print(f"Total Airdropped: {total_airdropped:,.2f} tokens")
    print(f"Total Remaining: {total_remaining:,.2f} tokens")
    print(f"Overall Retention: {overall_retention:.2f}%")
    print("="*50)
    print(f"Report saved to: {output_file}")


def main():
    """Main function."""
    # Load environment variables
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(base_dir, '.env')
    load_dotenv(env_path)
    
    # Get token mint from environment
    token_mint_str = os.getenv('TOKEN_MINT_ADDRESS')
    if not token_mint_str:
        print("Error: TOKEN_MINT_ADDRESS environment variable is required")
        sys.exit(1)
    
    try:
        token_mint = Pubkey.from_string(token_mint_str)
    except Exception as e:
        print(f"Error: Invalid token mint address: {e}")
        sys.exit(1)
    
    # Initialize RPC client
    rpc_url = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
    rpc_client = Client(rpc_url, commitment=Confirmed)
    
    # Find the most recent successful airdrop CSV
    reports_dir = os.path.join(base_dir, 'reports')
    successful_csv = None
    
    # Look for the most recent live run
    for item in sorted(os.listdir(reports_dir), reverse=True):
        if item.startswith('run_live_phase1_'):
            csv_path = os.path.join(reports_dir, item, 'airdrop_successful.csv')
            if os.path.exists(csv_path):
                successful_csv = csv_path
                break
    
    if not successful_csv:
        print("Error: No successful airdrop CSV found")
        sys.exit(1)
    
    print(f"Using airdrop data from: {successful_csv}")
    
    # Load airdrop data
    recipients = load_airdrop_data(successful_csv)
    if not recipients:
        sys.exit(1)
    
    # Check current holdings
    results = check_holdings(recipients, rpc_client, token_mint)
    
    # Generate report
    output_file = os.path.join(base_dir, 'reports', 'holdings_analysis.csv')
    generate_report(results, output_file)


if __name__ == "__main__":
    main()