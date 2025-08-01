#!/usr/bin/env python3
"""
Solana Token Airdrop Script for SOL NEMA Distribution
Distributes SOL NEMA tokens to recipients based on sol_nema_airdrop.csv
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv

from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.transaction import Transaction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from spl.token.client import Token
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address, create_associated_token_account, transfer_checked, TransferCheckedParams


@dataclass
class AirdropConfig:
    """Configuration for the airdrop script."""
    rpc_url: str
    source_keypair: Keypair
    token_mint: Pubkey
    csv_file_path: str
    phase: int
    dry_run: bool = False
    batch_size: int = 10
    delay_between_batches: float = 1.0
    rpc_check_delay: float = 1.0
    rpc_check_batch: int = 3
    max_retries: int = 3
    log_level: str = "INFO"
    progress_file: str = "airdrop_progress.json"
    token_decimals: int = 6


@dataclass
class Recipient:
    """Represents an airdrop recipient."""
    sol_wallet: str
    worm_balance: int
    sol_nema_tokens: float
    pubkey: Optional[Pubkey] = None
    token_account: Optional[Pubkey] = None
    status: str = "pending"  # pending, success, failed, skipped


class SolanaAirdropManager:
    """Manages the Solana token airdrop process."""

    def __init__(self, config: AirdropConfig):
        self.config = config
        self.recipients: List[Recipient] = []
        self.successful_transfers = 0
        self.failed_transfers = 0
        self.skipped_transfers = 0

        # Setup logging with phase info
        self._setup_logging()

        # Initialize RPC client
        self.rpc_client = Client(config.rpc_url, commitment=Confirmed)

        # Initialize SPL Token client
        self.token_client = Token(
            conn=self.rpc_client,
            pubkey=config.token_mint,
            program_id=TOKEN_PROGRAM_ID,
            payer=config.source_keypair
        )

        self.logger.info("Initialized SolanaAirdropManager")
        self.logger.info(f"Mode: {'DRY RUN' if config.dry_run else 'LIVE'}")
        self.logger.info(f"Phase: {config.phase}")
        self.logger.info(f"RPC URL: {config.rpc_url}")
        self.logger.info(f"Source Wallet: {config.source_keypair.pubkey()}")
        self.logger.info(f"Token Mint: {config.token_mint}")
        self.logger.info(f"CSV File: {config.csv_file_path}")

    def _setup_logging(self):
        """Setup logging configuration."""
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        timestamp = int(time.time())
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs_dir = os.path.join(base_dir, 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        log_filename = os.path.join(logs_dir, f'airdrop_phase{self.config.phase}_{timestamp}.log')
        logging.basicConfig(
            level=getattr(logging, self.config.log_level.upper()),
            format=log_format,
            handlers=[
                logging.FileHandler(log_filename),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)

        # Disable noisy HTTP request logs
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    def load_recipients(self) -> bool:
        """Load recipients from CSV file and validate data."""
        try:
            self.logger.info("Loading recipients from CSV...")

            if not os.path.exists(self.config.csv_file_path):
                self.logger.error(f"CSV file not found: {self.config.csv_file_path}")
                return False

            with open(self.config.csv_file_path, 'r') as f:
                reader = csv.DictReader(f)

                for row_num, row in enumerate(reader, start=2):  # Start at 2 to account for header
                    try:
                        recipient = self._parse_recipient_row(row, row_num)
                        if recipient:
                            self.recipients.append(recipient)
                    except Exception as e:
                        self.logger.error(f"Error parsing row {row_num}: {e}")
                        continue

            self.logger.info(f"Loaded {len(self.recipients)} recipients")
            return len(self.recipients) > 0

        except Exception as e:
            self.logger.error(f"Failed to load recipients: {e}")
            return False

    def _parse_recipient_row(self, row: dict, row_num: int) -> Optional[Recipient]:
        """Parse a single recipient row from CSV."""
        try:
            sol_wallet = row['sol_wallet'].strip()
            worm_balance = int(row['worm_balance'])

            # Get the correct phase column based on configuration
            phase_column = f'phase{self.config.phase}_tokens'
            if phase_column not in row:
                self.logger.error(f"Phase column '{phase_column}' not found in CSV at row {row_num}")
                return None

            sol_nema_tokens = float(row[phase_column])

            # Validate wallet address
            try:
                pubkey = Pubkey.from_string(sol_wallet)
            except Exception:
                self.logger.error(f"Invalid wallet address at row {row_num}: {sol_wallet}")
                return None

            # Validate token amount
            if sol_nema_tokens <= 0:
                self.logger.warning(f"Zero or negative token amount at row {row_num}: {sol_nema_tokens}")
                return None

            recipient = Recipient(
                sol_wallet=sol_wallet,
                worm_balance=worm_balance,
                sol_nema_tokens=sol_nema_tokens,
                pubkey=pubkey
            )

            return recipient

        except Exception as e:
            self.logger.error(f"Error parsing recipient at row {row_num}: {e}")
            return None

    def validate_configuration(self) -> bool:
        """Validate the airdrop configuration."""
        self.logger.info("Validating configuration...")

        try:
            # Check source wallet SOL balance for transaction fees
            sol_balance = self.rpc_client.get_balance(self.config.source_keypair.pubkey())
            sol_balance_sol = sol_balance.value / 1_000_000_000  # Convert lamports to SOL
            self.logger.info(f"Source wallet SOL balance: {sol_balance_sol:.4f} SOL")

            # Estimate minimum SOL needed (rough estimate: 0.01 SOL per transaction)
            estimated_sol_needed = len(self.recipients) * 0.01
            if sol_balance_sol < estimated_sol_needed:
                self.logger.error(f"Insufficient SOL for transaction fees. Need ~{estimated_sol_needed:.2f} SOL, have {sol_balance_sol:.4f} SOL")
                return False

            # Get source token account balance
            source_token_account = get_associated_token_address(
                self.config.source_keypair.pubkey(),
                self.config.token_mint
            )

            try:
                token_balance_resp = self.rpc_client.get_token_account_balance(source_token_account)
                source_token_balance_raw = float(token_balance_resp.value.amount)
                source_token_balance = source_token_balance_raw / (10 ** self.config.token_decimals)
                self.logger.info(f"Source token balance: {source_token_balance:,.0f} tokens")
            except Exception as e:
                self.logger.error(f"Could not get source token balance: {e}")
                return False

            # Calculate total tokens to distribute
            total_tokens = sum(r.sol_nema_tokens for r in self.recipients)
            self.logger.info(f"Total tokens to distribute: {total_tokens:,.2f}")

            # Check if source has sufficient tokens
            if source_token_balance < total_tokens:
                self.logger.error(f"Insufficient tokens in source wallet. Need {total_tokens:,.2f}, have {source_token_balance:,.0f}")
                return False

            # Validate total matches expected amount for this phase
            phase_expected = {
                1: 50_000_000,   # 5% of 1B
                2: 100_000_000,  # 10% of 1B
                3: 100_000_000,  # 10% of 1B
                4: 50_000_000    # 5% of 1B
            }
            expected_total = phase_expected.get(self.config.phase, 50_000_000)
            if abs(total_tokens - expected_total) > 1000:  # Allow small variance
                self.logger.warning(f"Phase {self.config.phase} tokens ({total_tokens:,.2f}) differs significantly from expected ({expected_total:,.2f})")

            return True

        except Exception as e:
            self.logger.error(f"Configuration validation failed: {e}")
            return False

    def check_and_create_token_accounts(self) -> bool:
        """Check and create missing associated token accounts for recipients."""
        self.logger.info("Checking associated token accounts for recipients...")

        accounts_to_create = []

        for i, recipient in enumerate(self.recipients):
            # Skip recipients with invalid pubkeys
            if recipient.pubkey is None:
                self.logger.error(f"Skipping recipient {recipient.sol_wallet} - invalid pubkey")
                recipient.status = "skipped"
                self.skipped_transfers += 1
                continue

            # Calculate associated token account address
            token_account = get_associated_token_address(
                recipient.pubkey,
                self.config.token_mint
            )
            recipient.token_account = token_account

            # Check if account exists with rate limiting
            try:
                account_info = self.rpc_client.get_account_info(token_account)
                if account_info.value is None:
                    # Account doesn't exist, needs to be created
                    accounts_to_create.append(recipient)
                    self.logger.debug(f"Token account needed for {recipient.sol_wallet}")
                else:
                    self.logger.debug(f"Token account exists for {recipient.sol_wallet}")
            except Exception as e:
                self.logger.error(f"Error checking token account for {recipient.sol_wallet}: {e}")
                accounts_to_create.append(recipient)  # Assume needs creation

            # Add delay every few requests to avoid rate limiting
            if (i + 1) % self.config.rpc_check_batch == 0:
                self.logger.debug(f"Processed {i + 1}/{len(self.recipients)} account checks, adding delay...")
                time.sleep(self.config.rpc_check_delay)

        self.logger.info(f"Found {len(accounts_to_create)} accounts that need to be created")

        if not accounts_to_create:
            return True

        # Create missing token accounts in batches
        return self._create_token_accounts_batch(accounts_to_create)

    def _create_token_accounts_batch(self, recipients: List[Recipient]) -> bool:
        """Create associated token accounts in batches."""
        self.logger.info(f"Creating {len(recipients)} associated token accounts...")

        if self.config.dry_run:
            self.logger.info("DRY RUN: Would create token accounts")
            return True

        success_count = 0
        batch_size = 5  # Conservative batch size for account creation

        for i in range(0, len(recipients), batch_size):
            batch = recipients[i:i + batch_size]
            self.logger.info(f"Creating accounts batch {i//batch_size + 1}/{(len(recipients) + batch_size - 1)//batch_size}")

            try:
                # Build transaction with multiple account creations
                transaction = Transaction()
                recent_blockhash = self.rpc_client.get_latest_blockhash().value.blockhash

                for recipient in batch:
                    # Skip if pubkey is None (should not happen at this point, but safety check)
                    if recipient.pubkey is None:
                        self.logger.error(f"Skipping account creation for {recipient.sol_wallet} - missing pubkey")
                        continue

                    create_account_ix = create_associated_token_account(
                        payer=self.config.source_keypair.pubkey(),
                        owner=recipient.pubkey,
                        mint=self.config.token_mint
                    )
                    transaction.add(create_account_ix)

                transaction.recent_blockhash = recent_blockhash

                # Send transaction
                result = self.rpc_client.send_transaction(
                    transaction,
                    self.config.source_keypair,
                    TxOpts(skip_confirmation=False, skip_preflight=False)
                )

                if result.value:
                    success_count += len(batch)
                    self.logger.info(f"Successfully created {len(batch)} token accounts: {result.value}")
                else:
                    self.logger.error("Failed to create token accounts for batch")

            except Exception as e:
                self.logger.error(f"Error creating token accounts batch: {e}")
                # Try individual creation for this batch
                for recipient in batch:
                    if self._create_single_token_account(recipient):
                        success_count += 1

            # Add delay between batches
            if i + batch_size < len(recipients):
                time.sleep(self.config.delay_between_batches)

        self.logger.info(f"Successfully created {success_count}/{len(recipients)} token accounts")
        return success_count == len(recipients)

    def _create_single_token_account(self, recipient: Recipient) -> bool:
        """Create a single associated token account."""
        try:
            # Skip if pubkey is None
            if recipient.pubkey is None:
                self.logger.error(f"Cannot create account for {recipient.sol_wallet} - missing pubkey")
                return False

            create_account_ix = create_associated_token_account(
                payer=self.config.source_keypair.pubkey(),
                owner=recipient.pubkey,
                mint=self.config.token_mint
            )

            transaction = Transaction()
            recent_blockhash = self.rpc_client.get_latest_blockhash().value.blockhash
            transaction.add(create_account_ix)
            transaction.recent_blockhash = recent_blockhash

            result = self.rpc_client.send_transaction(
                transaction,
                self.config.source_keypair,
                opts=TxOpts(skip_confirmation=False, skip_preflight=False)
            )

            if result.value:
                self.logger.debug(f"Created token account for {recipient.sol_wallet}")
                return True
            else:
                self.logger.error(f"Failed to create token account for {recipient.sol_wallet}")
                return False

        except Exception as e:
            self.logger.error(f"Error creating token account for {recipient.sol_wallet}: {e}")
            return False

    def execute_token_transfers(self) -> bool:
        """Execute token transfers to all recipients."""
        self.logger.info("Starting token transfers...")

        if self.config.dry_run:
            self.logger.info("DRY RUN: Would transfer tokens to all recipients")
            self.successful_transfers = len(self.recipients)
            return True

        # Get source token account
        source_token_account = get_associated_token_address(
            self.config.source_keypair.pubkey(),
            self.config.token_mint
        )

        # Execute transfers in batches
        batch_size = self.config.batch_size
        total_batches = (len(self.recipients) + batch_size - 1) // batch_size

        for i in range(0, len(self.recipients), batch_size):
            batch = self.recipients[i:i + batch_size]
            batch_num = i // batch_size + 1

            self.logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} recipients)")

            if self._execute_transfer_batch(batch, source_token_account):
                self.successful_transfers += len(batch)
            else:
                # If batch fails, try individual transfers
                for recipient in batch:
                    if self._execute_single_transfer(recipient, source_token_account):
                        self.successful_transfers += 1
                        recipient.status = "success"
                    else:
                        self.failed_transfers += 1
                        recipient.status = "failed"

            # Add delay between batches
            if i + batch_size < len(self.recipients):
                time.sleep(self.config.delay_between_batches)

        success_rate = (self.successful_transfers / len(self.recipients)) * 100
        self.logger.info(f"Token transfers completed. Success rate: {success_rate:.2f}%")

        return self.successful_transfers > 0

    def _execute_transfer_batch(self, recipients: List[Recipient], source_token_account: Pubkey) -> bool:
        """Execute token transfers for a batch of recipients."""
        try:
            transaction = Transaction()
            recent_blockhash = self.rpc_client.get_latest_blockhash().value.blockhash

            for recipient in recipients:
                # Skip recipients with missing pubkey or token account
                if recipient.pubkey is None or recipient.token_account is None:
                    self.logger.error(f"Skipping transfer for {recipient.sol_wallet} - missing pubkey or token account")
                    continue

                # Convert token amount to proper decimal places based on token decimals
                token_amount_raw = int(recipient.sol_nema_tokens * (10 ** self.config.token_decimals))

                transfer_ix = transfer_checked(
                    TransferCheckedParams(
                        program_id=TOKEN_PROGRAM_ID,
                        source=source_token_account,
                        mint=self.config.token_mint,
                        dest=recipient.token_account,
                        owner=self.config.source_keypair.pubkey(),
                        amount=token_amount_raw,
                        decimals=self.config.token_decimals,
                    )
                )
                transaction.add(transfer_ix)

            transaction.recent_blockhash = recent_blockhash

            # Send transaction
            result = self.rpc_client.send_transaction(
                transaction,
                self.config.source_keypair,
                opts=TxOpts(skip_confirmation=False, skip_preflight=False)
            )

            if result.value:
                total_tokens = sum(r.sol_nema_tokens for r in recipients)
                self.logger.info(f"Batch transfer successful: {result.value} ({total_tokens:,.2f} tokens)")
                for recipient in recipients:
                    recipient.status = "success"
                return True
            else:
                self.logger.error("Batch transfer failed")
                return False

        except Exception as e:
            self.logger.error(f"Error in batch transfer: {e}")
            return False

    def _execute_single_transfer(self, recipient: Recipient, source_token_account: Pubkey) -> bool:
        """Execute a single token transfer with retry logic."""
        # Skip recipients with missing pubkey or token account
        if recipient.pubkey is None or recipient.token_account is None:
            self.logger.error(f"Cannot transfer to {recipient.sol_wallet} - missing pubkey or token account")
            return False

        for attempt in range(self.config.max_retries):
            try:
                # Convert token amount to proper decimal places based on token decimals
                token_amount_raw = int(recipient.sol_nema_tokens * (10 ** self.config.token_decimals))

                transfer_ix = transfer_checked(
                    TransferCheckedParams(
                        program_id=TOKEN_PROGRAM_ID,
                        source=source_token_account,
                        mint=self.config.token_mint,
                        dest=recipient.token_account,
                        owner=self.config.source_keypair.pubkey(),
                        amount=token_amount_raw,
                        decimals=self.config.token_decimals,
                    )
                )

                transaction = Transaction()
                recent_blockhash = self.rpc_client.get_latest_blockhash().value.blockhash
                transaction.add(transfer_ix)
                transaction.recent_blockhash = recent_blockhash

                result = self.rpc_client.send_transaction(
                    transaction,
                    self.config.source_keypair,
                    TxOpts(skip_confirmation=False, skip_preflight=False)
                )

                if result.value:
                    self.logger.debug(f"Transfer successful to {recipient.sol_wallet}: {recipient.sol_nema_tokens:,.2f} tokens")
                    return True
                else:
                    self.logger.warning(f"Transfer failed to {recipient.sol_wallet} (attempt {attempt + 1})")

            except Exception as e:
                self.logger.error(f"Error transferring to {recipient.sol_wallet} (attempt {attempt + 1}): {e}")

            # Wait before retry
            if attempt < self.config.max_retries - 1:
                time.sleep(1.0)

        self.logger.error(f"Failed to transfer to {recipient.sol_wallet} after {self.config.max_retries} attempts")
        return False

    def run_airdrop(self) -> bool:
        """Main method to execute the airdrop."""
        try:
            self.logger.info("Starting airdrop process...")

            # Load recipients
            if not self.load_recipients():
                return False

            # Validate configuration
            if not self.validate_configuration():
                return False

            # Check and create associated token accounts
            if not self.check_and_create_token_accounts():
                self.logger.error("Failed to create required token accounts")
                return False

            # Execute token transfers with resume capability
            if not self.execute_token_transfers_with_resume():
                self.logger.error("Token transfer process failed")
                return False

            self.logger.info("Airdrop process completed successfully")
            return True

        except Exception as e:
            self.logger.error(f"Airdrop failed: {e}")
            return False

    def save_progress(self):
        """Save current progress to file for recovery."""
        try:
            progress_data = {
                'timestamp': int(time.time()),
                'phase': self.config.phase,
                'total_recipients': len(self.recipients),
                'successful_transfers': self.successful_transfers,
                'failed_transfers': self.failed_transfers,
                'skipped_transfers': self.skipped_transfers,
                'recipients': [
                    {
                        'sol_wallet': r.sol_wallet,
                        'sol_nema_tokens': r.sol_nema_tokens,
                        'status': r.status
                    }
                    for r in self.recipients
                ]
            }

            with open(self.config.progress_file, 'w') as f:
                json.dump(progress_data, f, indent=2)

            self.logger.debug(f"Progress saved to {self.config.progress_file}")

        except Exception as e:
            self.logger.error(f"Failed to save progress: {e}")

    def load_progress(self) -> bool:
        """Load previous progress from file."""
        try:
            if not os.path.exists(self.config.progress_file):
                self.logger.info("No previous progress file found")
                return False

            with open(self.config.progress_file, 'r') as f:
                progress_data = json.load(f)

            # Check if progress file is for the same phase and recipients
            if progress_data.get('phase') != self.config.phase:
                self.logger.warning(f"Progress file is for phase {progress_data.get('phase')}, but current phase is {self.config.phase}")
                return False

            if progress_data.get('total_recipients') != len(self.recipients):
                self.logger.warning("Progress file recipient count doesn't match current recipients")
                return False

            # Restore progress counters
            self.successful_transfers = progress_data.get('successful_transfers', 0)
            self.failed_transfers = progress_data.get('failed_transfers', 0)
            self.skipped_transfers = progress_data.get('skipped_transfers', 0)

            # Restore recipient statuses
            progress_recipients = {r['sol_wallet']: r['status'] for r in progress_data.get('recipients', [])}

            for recipient in self.recipients:
                if recipient.sol_wallet in progress_recipients:
                    recipient.status = progress_recipients[recipient.sol_wallet]

            completed_count = sum(1 for r in self.recipients if r.status in ['success', 'failed', 'skipped'])
            self.logger.info(f"Loaded progress: {completed_count}/{len(self.recipients)} recipients processed")

            return True

        except Exception as e:
            self.logger.error(f"Failed to load progress: {e}")
            return False

    def get_pending_recipients(self) -> List[Recipient]:
        """Get list of recipients that still need processing."""
        return [r for r in self.recipients if r.status == 'pending']

    def execute_token_transfers_with_resume(self) -> bool:
        """Execute token transfers with ability to resume from previous progress."""
        # Load previous progress if available
        self.load_progress()

        # Get pending recipients
        pending_recipients = self.get_pending_recipients()

        if not pending_recipients:
            self.logger.info("All recipients have already been processed")
            return True

        self.logger.info(f"Resuming airdrop: {len(pending_recipients)} recipients remaining")

        if self.config.dry_run:
            self.logger.info("DRY RUN: Would transfer tokens to remaining recipients")
            for recipient in pending_recipients:
                recipient.status = "success"
                self.successful_transfers += 1
            # Don't save progress during dry runs to avoid interfering with live runs
            return True

        # Get source token account
        source_token_account = get_associated_token_address(
            self.config.source_keypair.pubkey(),
            self.config.token_mint
        )

        # Execute transfers in batches
        batch_size = self.config.batch_size
        total_batches = (len(pending_recipients) + batch_size - 1) // batch_size

        for i in range(0, len(pending_recipients), batch_size):
            batch = pending_recipients[i:i + batch_size]
            batch_num = i // batch_size + 1

            self.logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} recipients)")

            if self._execute_transfer_batch(batch, source_token_account):
                self.successful_transfers += len(batch)
                for recipient in batch:
                    recipient.status = "success"
            else:
                # If batch fails, try individual transfers
                for recipient in batch:
                    if self._execute_single_transfer(recipient, source_token_account):
                        self.successful_transfers += 1
                        recipient.status = "success"
                    else:
                        self.failed_transfers += 1
                        recipient.status = "failed"

            # Save progress after each batch
            self.save_progress()

            # Add delay between batches
            if i + batch_size < len(pending_recipients):
                time.sleep(self.config.delay_between_batches)

        success_rate = (self.successful_transfers / len(self.recipients)) * 100
        self.logger.info(f"Token transfers completed. Success rate: {success_rate:.2f}%")

        return self.successful_transfers > 0

    def generate_report(self):
        """Generate comprehensive airdrop report."""
        total_recipients = len(self.recipients)

        # Calculate totals
        successful_tokens = sum(r.sol_nema_tokens for r in self.recipients if r.status == "success")
        failed_tokens = sum(r.sol_nema_tokens for r in self.recipients if r.status == "failed")
        total_tokens = sum(r.sol_nema_tokens for r in self.recipients)

        # Console report
        self.logger.info("=" * 50)
        self.logger.info("         SOLANA AIRDROP FINAL REPORT")
        self.logger.info("=" * 50)
        self.logger.info(f"Total Recipients: {total_recipients:,}")
        self.logger.info(f"Successful Transfers: {self.successful_transfers:,}")
        self.logger.info(f"Failed Transfers: {self.failed_transfers:,}")
        self.logger.info(f"Skipped Transfers: {self.skipped_transfers:,}")
        self.logger.info("-" * 50)
        self.logger.info(f"Total Tokens Distributed: {successful_tokens:,.2f}")
        self.logger.info(f"Tokens Failed to Distribute: {failed_tokens:,.2f}")
        self.logger.info(f"Total Tokens in Airdrop: {total_tokens:,.2f}")

        success_rate = (self.successful_transfers / total_recipients * 100) if total_recipients > 0 else 0
        token_success_rate = (successful_tokens / total_tokens * 100) if total_tokens > 0 else 0

        self.logger.info("-" * 50)
        self.logger.info(f"Transfer Success Rate: {success_rate:.2f}%")
        self.logger.info(f"Token Distribution Rate: {token_success_rate:.2f}%")
        self.logger.info("=" * 50)

        # Generate detailed CSV reports
        self._generate_csv_reports()

        # Log failed transfers if any
        if self.failed_transfers > 0:
            self.logger.warning("Failed transfers:")
            for recipient in self.recipients:
                if recipient.status == "failed":
                    self.logger.warning(f"  {recipient.sol_wallet}: {recipient.sol_nema_tokens:,.2f} tokens")

    def _generate_csv_reports(self):
        """Generate detailed CSV reports."""
        timestamp = int(time.time())

        # Create reports directory for this run
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        run_prefix = "run_dry" if self.config.dry_run else "run_live"
        run_dir = os.path.join(base_dir, 'reports', f"{run_prefix}_phase{self.config.phase}_{timestamp}")
        os.makedirs(run_dir, exist_ok=True)

        # Generate report file paths
        success_file = os.path.join(run_dir, "airdrop_successful.csv")
        failed_file = os.path.join(run_dir, "airdrop_failed.csv")
        summary_file = os.path.join(run_dir, "airdrop_summary.csv")

        try:
            # Successful transfers report
            successful_recipients = [r for r in self.recipients if r.status == "success"]
            if successful_recipients:
                with open(success_file, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['sol_wallet', 'worm_balance', 'sol_nema_tokens', 'status'])
                    for recipient in successful_recipients:
                        writer.writerow([
                            recipient.sol_wallet,
                            recipient.worm_balance,
                            recipient.sol_nema_tokens,
                            recipient.status
                        ])
                self.logger.info(f"Successful transfers report: {success_file}")

            # Failed transfers report
            failed_recipients = [r for r in self.recipients if r.status == "failed"]
            if failed_recipients:
                with open(failed_file, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['sol_wallet', 'worm_balance', 'sol_nema_tokens', 'status'])
                    for recipient in failed_recipients:
                        writer.writerow([
                            recipient.sol_wallet,
                            recipient.worm_balance,
                            recipient.sol_nema_tokens,
                            recipient.status
                        ])
                self.logger.info(f"Failed transfers report: {failed_file}")

            # Summary report
            with open(summary_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['metric', 'value'])
                writer.writerow(['timestamp', timestamp])
                writer.writerow(['phase', self.config.phase])
                writer.writerow(['total_recipients', len(self.recipients)])
                writer.writerow(['successful_transfers', self.successful_transfers])
                writer.writerow(['failed_transfers', self.failed_transfers])
                writer.writerow(['skipped_transfers', self.skipped_transfers])

                successful_tokens = sum(r.sol_nema_tokens for r in self.recipients if r.status == "success")
                failed_tokens = sum(r.sol_nema_tokens for r in self.recipients if r.status == "failed")
                total_tokens = sum(r.sol_nema_tokens for r in self.recipients)

                writer.writerow(['successful_tokens', successful_tokens])
                writer.writerow(['failed_tokens', failed_tokens])
                writer.writerow(['total_tokens', total_tokens])

                success_rate = (self.successful_transfers / len(self.recipients) * 100) if len(self.recipients) > 0 else 0
                token_success_rate = (successful_tokens / total_tokens * 100) if total_tokens > 0 else 0

                writer.writerow(['transfer_success_rate_percent', f"{success_rate:.2f}"])
                writer.writerow(['token_success_rate_percent', f"{token_success_rate:.2f}"])
                writer.writerow(['dry_run', self.config.dry_run])
                writer.writerow(['token_mint', str(self.config.token_mint)])
                writer.writerow(['rpc_url', self.config.rpc_url])

            self.logger.info(f"Summary report: {summary_file}")

        except Exception as e:
            self.logger.error(f"Failed to generate CSV reports: {e}")


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="SOL NEMA Token Airdrop Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables Required:
  AIRDROP_PRIVATE_KEY    Base58-encoded private key of source wallet
  TOKEN_MINT_ADDRESS     SPL token mint address for SOL NEMA tokens

Optional Environment Variables:
  SOLANA_RPC_URL         Solana RPC endpoint (default: mainnet-beta)
  CSV_FILE_PATH         Path to CSV file (default: data/sol_nema_airdrop.csv)
  BATCH_SIZE            Number of transfers per batch (default: 10)
  BATCH_DELAY           Delay between batches in seconds (default: 1.0)
  RPC_CHECK_DELAY       Delay between RPC account checks in seconds (default: 1.0)
  RPC_CHECK_BATCH       Number of checks before adding delay (default: 3)
  MAX_RETRIES           Maximum retry attempts (default: 3)
  LOG_LEVEL             Logging level (default: INFO)
  TOKEN_DECIMALS        Number of token decimals (default: 6)
  PROGRESS_FILE         Progress file path (default: airdrop_progress.json)

Examples:
  # Dry run mode
  python script/airdrop.py --dry-run

  # Live execution (after testing with dry run)
  python script/airdrop.py
        """
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run in test mode without executing actual transfers'
    )

    parser.add_argument(
        '--phase',
        type=int,
        required=True,
        choices=[1, 2, 3, 4],
        help='Airdrop phase to execute (1=5%% 1-day, 2=10%% 2-week, 3=10%% 2-month, 4=5%% 3-month)'
    )

    return parser.parse_args()

def load_config_from_env(args=None) -> AirdropConfig:
    """Load configuration from environment variables and command line arguments."""

    # Get source wallet private key
    private_key_b58 = os.getenv('AIRDROP_PRIVATE_KEY')
    if not private_key_b58:
        raise ValueError("AIRDROP_PRIVATE_KEY environment variable is required")

    try:
        source_keypair = Keypair.from_base58_string(private_key_b58)
    except Exception as e:
        raise ValueError(f"Invalid private key format: {e}")

    # Get token mint address
    token_mint_str = os.getenv('TOKEN_MINT_ADDRESS')
    if not token_mint_str:
        raise ValueError("TOKEN_MINT_ADDRESS environment variable is required")

    try:
        token_mint = Pubkey.from_string(token_mint_str)
    except Exception as e:
        raise ValueError(f"Invalid token mint address: {e}")

    # Determine CSV file path from env var or default
    csv_file_path = os.getenv('CSV_FILE_PATH')
    if not csv_file_path:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        csv_file_path = os.path.join(base_dir, 'data', 'sol_nema_airdrop.csv')

    # Build configuration using environment variables
    config = AirdropConfig(
        rpc_url=os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com'),
        source_keypair=source_keypair,
        token_mint=token_mint,
        csv_file_path=csv_file_path,
        phase=args.phase if args else 1,
        dry_run=args.dry_run if args else False,
        batch_size=int(os.getenv('BATCH_SIZE', '10')),
        delay_between_batches=float(os.getenv('BATCH_DELAY', '1.0')),
        rpc_check_delay=float(os.getenv('RPC_CHECK_DELAY', '1.0')),
        rpc_check_batch=int(os.getenv('RPC_CHECK_BATCH', '3')),
        max_retries=int(os.getenv('MAX_RETRIES', '3')),
        log_level=os.getenv('LOG_LEVEL', 'INFO'),
        progress_file=os.getenv('PROGRESS_FILE', f'airdrop_phase{args.phase if args else 1}_progress.json'),
        token_decimals=int(os.getenv('TOKEN_DECIMALS', '6'))
    )

    return config


def main():
    """Main entry point for the airdrop script."""
    try:
        # Load environment variables from .env file
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_path = os.path.join(base_dir, '.env')
        load_dotenv(env_path)

        print("SOL NEMA Token Airdrop Script")
        print("=" * 40)

        # Parse command line arguments
        args = parse_arguments()

        # Load configuration
        config = load_config_from_env(args)

        # Display configuration summary
        print("Configuration:")
        print(f"  Phase: {config.phase}")
        print(f"  RPC URL: {config.rpc_url}")
        print(f"  CSV File: {config.csv_file_path}")
        print(f"  Mode: {'DRY RUN' if config.dry_run else 'LIVE EXECUTION'}")
        print(f"  Batch Size: {config.batch_size}")
        print(f"  Batch Delay: {config.delay_between_batches}s")
        print(f"  RPC Check Delay: {config.rpc_check_delay}s")
        print(f"  RPC Check Batch: {config.rpc_check_batch}")
        print(f"  Max Retries: {config.max_retries}")
        print(f"  Token Decimals: {config.token_decimals}")
        print(f"  Log Level: {config.log_level}")
        print()

        if not config.dry_run:
            confirmation = input("⚠️  LIVE MODE: This will execute real token transfers. Continue? (yes/no): ")
            if confirmation.lower() not in ['yes', 'y']:
                print("Operation cancelled.")
                sys.exit(0)

        # Create airdrop manager
        airdrop_manager = SolanaAirdropManager(config)

        # Run the airdrop
        success = airdrop_manager.run_airdrop()

        # Generate report
        airdrop_manager.generate_report()

        if success:
            print("\n✅ Airdrop completed successfully!")
            sys.exit(0)
        else:
            print("\n❌ Airdrop failed!")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n⚠️  Operation interrupted by user")
        print("Progress has been saved and can be resumed by running the script again.")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
