# SOL NEMA Token Airdrop

This repository contains the scripts and data for distributing SOL NEMA tokens to WORM token holders via airdrop on the Solana blockchain.

## Repository Structure

```
airdrop/
├── scripts/           # Python scripts for airdrop execution
│   ├── airdrop.py    # Main airdrop execution script
│   └── sol_nema.py   # Token distribution calculation script
├── data/             # CSV data files
│   ├── worm_holders.csv
│   ├── sol_eth_wallet_map.csv
│   └── sol_nema_airdrop.csv
├── reports/          # Generated reports from airdrop runs
├── logs/             # Progress and log files
├── venv/             # Python virtual environment (created during setup)
├── .env              # Environment variables (create from .env.example)
├── .env.example      # Example environment configuration
└── requirements.txt  # Python dependencies
```

## Airdrop Phases

The airdrop is distributed across 4 phases based on WORM token ownership:

- **Phase 1**: 5% of total supply (50M tokens) - 1 day after bond
- **Phase 2**: 10% of total supply (100M tokens) - 2 weeks after bond  
- **Phase 3**: 10% of total supply (100M tokens) - 2 months after bond
- **Phase 4**: 5% of total supply (50M tokens) - 3 months (holders only)

**Total Airdrop**: 30% of 1B total supply = 300M tokens

## Setup

1. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On macOS/Linux
# or
venv\Scripts\activate  # On Windows
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
# Copy the example environment file to create your configuration
cp .env.example .env

# Edit .env file with your actual values
# Required variables:
AIRDROP_PRIVATE_KEY=<base58-encoded-private-key>
TOKEN_MINT_ADDRESS=<spl-token-mint-address>

# Optional variables (defaults provided):
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
CSV_FILE_PATH=data/sol_nema_airdrop.csv
BATCH_SIZE=10
BATCH_DELAY=1.0
LOG_LEVEL=INFO
```

## Usage

### Calculate Token Distribution

Run the distribution calculation script to generate the airdrop CSV:

```bash
python scripts/sol_nema.py
```

This will read the WORM holder data and generate `data/sol_nema_airdrop.csv` with calculated token amounts for each phase.

### Execute Airdrop

#### Dry Run (Testing)
Always test first with a dry run:

```bash
python scripts/airdrop.py --phase 1 --dry-run
```

#### Live Execution
After testing, execute the actual airdrop:

```bash
python scripts/airdrop.py --phase 1
```

### Command Line Options

- `--phase {1,2,3,4}`: Required. Specify which airdrop phase to execute
- `--dry-run`: Optional. Run in test mode without executing real transfers

### Environment Variables

**Required:**
- `AIRDROP_PRIVATE_KEY`: Base58-encoded private key of the source wallet
- `TOKEN_MINT_ADDRESS`: SPL token mint address for SOL NEMA tokens

**Optional:**
- `SOLANA_RPC_URL`: Solana RPC endpoint (default: mainnet-beta)
- `CSV_FILE_PATH`: Path to CSV file (default: data/sol_nema_airdrop.csv)
- `BATCH_SIZE`: Number of transfers per batch (default: 10)
- `BATCH_DELAY`: Delay between batches in seconds (default: 1.0)
- `RPC_CHECK_DELAY`: Delay between RPC account checks in seconds (default: 1.0)
- `RPC_CHECK_BATCH`: Number of checks before adding delay (default: 3)
- `MAX_RETRIES`: Maximum retry attempts (default: 3)
- `LOG_LEVEL`: Logging level (default: INFO)
- `TOKEN_DECIMALS`: Number of token decimals (default: 6)
- `PROGRESS_FILE`: Progress file path (auto-generated per phase if not set)

## Features

- **Resume Capability**: Automatically resumes from where it left off if interrupted
- **Progress Tracking**: Saves progress to JSON files for recovery
- **Batch Processing**: Processes transfers in configurable batches
- **Rate Limiting**: Built-in delays to avoid RPC rate limits
- **Comprehensive Reporting**: Generates detailed CSV reports and logs
- **Account Creation**: Automatically creates associated token accounts if needed
- **Validation**: Validates wallet addresses, token amounts, and balances before execution

## Reports

After each run, detailed reports are generated in the `reports/` directory:
- `airdrop_successful.csv`: Successfully completed transfers
- `airdrop_failed.csv`: Failed transfers (if any)
- `airdrop_summary.csv`: Overall statistics and metrics

## File Locations

- **Environment**: `.env` file in repository root (copy from `.env.example`)
- **Data**: CSV files in `data/` directory
- **Logs**: Execution logs stored in `logs/` directory 
- **Reports**: Generated reports in `reports/` directory
- **Progress**: Progress files in repository root (e.g., `airdrop_phase1_progress.json`)

## Security

- Always test with `--dry-run` first
- Keep private keys secure and never commit them to version control
- The `.env` file is gitignored to prevent accidental commits
- Use `.env.example` as a template for setting up your environment
- Verify all addresses and amounts before live execution
- Monitor transaction fees and RPC rate limits