import csv
import os


def calculate_sol_nema_distribution():
    # Get the absolute paths for the CSV files
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wallets_path = os.path.join(base_dir, 'data', 'worm_holders.csv')
    sol_eth_path = os.path.join(base_dir, 'data', 'sol_eth_wallet_map.csv')

    # Create a dictionary to store wallet balances
    worm_balances: dict[str, int] = {}  # sol_wallet -> worm_balance

    # Read wallets.csv to get balances
    with open(wallets_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            worm_balances[row['wallet']] = int(row['balance'])

    # Read sol_eth_wallet_map.csv and get unique SOL wallets
    sol_wallets = set()
    sol_balances: dict[str, int] = {}  # sol_wallet -> worm_balance

    with open(sol_eth_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sol_wallet = row['sol_wallet']
            sol_wallets.add(sol_wallet)

    # Map SOL wallets to their worm balances
    total_worm_claimed = 0
    for sol_wallet in sol_wallets:
        balance = worm_balances.get(sol_wallet, 0)

        if balance == 0:
            print(f"Warning: {sol_wallet} has no worm balance")
            continue

        sol_balances[sol_wallet] = balance
        total_worm_claimed += balance

    # Print results
    print(f"Total claimed WORM tokens: {total_worm_claimed:,}")
    print(f"Total SOL wallets eligible: {len(sol_balances)}")

    # Calculate SOL NEMA airdrop amounts based on WORM token ownership percentage
    # Total supply is 1B tokens, airdrop phases are percentages of total supply:
    # Phase 1: 5% of 1B = 50M tokens (1 day after bond)
    # Phase 2: 10% of 1B = 100M tokens (2 weeks after bond) 
    # Phase 3: 10% of 1B = 100M tokens (2 months after bond)
    # Phase 4: 5% of 1B = 50M tokens (holders from bonding to 3 months)
    # Total airdrop: 30% of 1B = 300M tokens
    total_sol_nema_supply = 1_000_000_000  # 1 billion total supply
    phase_percentages = {
        'phase1': 0.05,  # 5% of total supply - 1 day after bond
        'phase2': 0.10,  # 10% of total supply - 2 weeks after bond
        'phase3': 0.10,  # 10% of total supply - 2 months after bond
        'phase4': 0.05   # 5% of total supply - 3 months (holders only)
    }
    
    # Calculate phase amounts
    phase_amounts = {}
    for phase, percentage in phase_percentages.items():
        phase_amounts[phase] = int(total_sol_nema_supply * percentage)
    
    print("Phase Distribution:")
    for phase, amount in phase_amounts.items():
        print(f"  {phase}: {amount:,} tokens ({phase_percentages[phase]*100}%)")

    # Create final results dictionary
    sol_results: dict[str, dict[str, int | float]] = {}

    for sol_wallet in sol_balances:
        worm_balance = sol_balances[sol_wallet]
        ownership_percentage = worm_balance / total_worm_claimed
        
        # Calculate each phase amount based on ownership percentage
        phase1_amount = round(phase_amounts['phase1'] * ownership_percentage, 2)
        phase2_amount = round(phase_amounts['phase2'] * ownership_percentage, 2)
        phase3_amount = round(phase_amounts['phase3'] * ownership_percentage, 2)
        phase4_amount = round(phase_amounts['phase4'] * ownership_percentage, 2)
        total_amount = round(phase1_amount + phase2_amount + phase3_amount + phase4_amount, 2)
        
        sol_results[sol_wallet] = {
            'worm_balance': worm_balance,
            'phase1_tokens': phase1_amount,
            'phase2_tokens': phase2_amount, 
            'phase3_tokens': phase3_amount,
            'phase4_tokens': phase4_amount,
            'total_tokens': total_amount
        }

    # Calculate totals for verification
    phase_totals = {
        'phase1': sum(data['phase1_tokens'] for data in sol_results.values()),
        'phase2': sum(data['phase2_tokens'] for data in sol_results.values()),
        'phase3': sum(data['phase3_tokens'] for data in sol_results.values()),
        'phase4': sum(data['phase4_tokens'] for data in sol_results.values()),
        'total': sum(data['total_tokens'] for data in sol_results.values())
    }
    
    print("\nDistribution Totals:")
    for phase, total in phase_totals.items():
        print(f"  {phase}: {total:,} tokens")

    # Write out the results to a CSV file
    output_path = os.path.join(base_dir, 'data', 'sol_nema_airdrop.csv')
    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'sol_wallet', 
            'worm_balance', 
            'phase1_tokens',  # 5% - 1 day after bond
            'phase2_tokens',  # 10% - 2 weeks after bond
            'phase3_tokens',  # 10% - 2 months after bond
            'phase4_tokens',  # 5% - 3 months (holders only)
            'total_tokens'    # Sum of all phases
        ])
        for sol_wallet, data in sol_results.items():
            writer.writerow([
                sol_wallet,
                data['worm_balance'],
                data['phase1_tokens'],
                data['phase2_tokens'],
                data['phase3_tokens'],
                data['phase4_tokens'],
                data['total_tokens']
            ])

    print(f"Results written to: {output_path}")


if __name__ == "__main__":
    calculate_sol_nema_distribution()
