#!/usr/bin/env python3
"""
Download latest Versa FlexVNF images from builds.versa-networks.com
Downloads BOTH Sandybridge and Westmere versions
"""

import os
import sys
import subprocess
import re
import argparse
import logging
from pathlib import Path
from datetime import datetime
from html.parser import HTMLParser

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class DirectoryParser(HTMLParser):
    """Parse HTML directory listing to extract file links."""
    
    def __init__(self):
        super().__init__()
        self.files = []
        
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for attr, value in attrs:
                if attr == 'href':
                    # Skip parent directory links and navigation
                    if value and not value.startswith('?') and not value.startswith('/') and value != '../':
                        self.files.append(value)


class ImageDownloader:
    """Download and manage Versa FlexVNF images for both architectures"""
    
    def __init__(self, build_version, manual_filename_snb=None, manual_filename_wsm=None):
        self.build_version = build_version
        self.manual_filename_snb = manual_filename_snb
        self.manual_filename_wsm = manual_filename_wsm
        self.version_path = None
        self.base_url = None
        self.snb_dir = None
        self.wsm_dir = None
        
    def set_version_path(self):
        """Convert version like 23.1.1 to 23.1"""
        parts = self.build_version.split('.')
        self.version_path = f"{parts[0]}.{parts[1]}"
        logger.info(f"Version path: {self.version_path}")
        return self.version_path
    
    def set_directories(self):
        """Set download URLs and destination directories"""
        self.base_url = f"https://builds.versa-networks.com/versa-flexvnf/{self.version_path}/latest/jammy"
        
        base_dir = "/home/versa/git/ansible_automation/Upgrade_Testing/vos_release_build"
        version_dir = self.build_version.replace('.', '_')
        
        self.snb_dir = f"{base_dir}/{version_dir}/snb/"
        self.wsm_dir = f"{base_dir}/{version_dir}/wsm/"
        
        logger.info(f"Base URL: {self.base_url}")
        logger.info(f"SNB directory: {self.snb_dir}")
        logger.info(f"WSM directory: {self.wsm_dir}")
    
    def create_directories(self):
        """Ensure the target directories exist"""
        os.makedirs(self.snb_dir, exist_ok=True)
        os.makedirs(self.wsm_dir, exist_ok=True)
        logger.info("Directories prepared")
    
    def cleanup_old_files(self, directory, arch_name):
        """Remove old .bin files from directory"""
        logger.info(f"Cleaning up old files in {arch_name} directory...")
        deleted_count = 0
        try:
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                if os.path.isfile(file_path) and filename.endswith('.bin'):
                    os.remove(file_path)
                    logger.info(f"  Deleted: {filename}")
                    deleted_count += 1
            
            if deleted_count == 0:
                logger.info(f"  No old files to delete")
            else:
                logger.info(f"  Deleted {deleted_count} file(s)")
        except Exception as e:
            logger.warning(f"  Error cleaning {arch_name} directory: {e}")
    
    def get_matching_filename(self, url, pattern, arch_name):
        """Get the filename that matches the specified pattern from directory listing"""
        try:
            logger.info(f"Fetching directory listing from: {url}")
            
            # Fetch directory listing
            result = subprocess.run(
                ['wget', '--quiet', '-O', '-', url],
                capture_output=True,
                text=True,
                check=True,
                timeout=30
            )
            
            # Parse HTML to extract file links
            parser = DirectoryParser()
            parser.feed(result.stdout)
            
            logger.info(f"Found {len(parser.files)} items in directory")
            
            # Filter files by the specified pattern
            matching_files = []
            for filename in parser.files:
                if re.match(pattern, filename):
                    matching_files.append(filename)
                    logger.info(f"  ✓ Match: {filename}")
            
            if matching_files:
                filename = matching_files[0]
                logger.info(f"✓ Selected {arch_name} file: {filename}")
                return filename
            else:
                logger.error(f"✗ No file matching pattern '{pattern}' found for {arch_name}")
                return None
                
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout fetching directory listing from {url}")
            return None
        except subprocess.CalledProcessError as e:
            logger.error(f"Error fetching directory listing from {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing directory listing: {e}")
            return None
    
    def download_file(self, url, output_path, arch_name):
        """Download a file using wget with progress indicator"""
        logger.info(f"Downloading {arch_name} image...")
        logger.info(f"  From: {url}")
        logger.info(f"  To: {output_path}")
        
        try:
            # Use wget with progress bar
            result = subprocess.run(
                ['wget', '--progress=bar:force', '--timeout=300', '--tries=3', '-O', output_path, url],
                check=True,
                timeout=1800  # 30 minute timeout
            )
            
            # Check if file was created and has content
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                file_size = os.path.getsize(output_path)
                file_size_mb = file_size / (1024 * 1024)
                logger.info(f"✓ Download successful: {output_path}")
                logger.info(f"  File size: {file_size_mb:.2f} MB")
                return True
            else:
                logger.error(f"✗ Download failed: File not created or empty")
                if os.path.exists(output_path):
                    os.remove(output_path)
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"✗ Download timeout after 30 minutes")
            if os.path.exists(output_path):
                os.remove(output_path)
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"✗ Download failed with error code {e.returncode}")
            if os.path.exists(output_path):
                os.remove(output_path)
            return False
        except Exception as e:
            logger.error(f"✗ Download failed: {e}")
            if os.path.exists(output_path):
                os.remove(output_path)
            return False
    
    def log_download(self, arch, filename, path):
        """Log download to file"""
        log_file = "/var/log/ansible/image_downloads.log"
        log_dir = os.path.dirname(log_file)
        
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_entry = f"[{timestamp}] Downloaded {filename} ({arch}) to {path}\n"
            
            with open(log_file, 'a') as f:
                f.write(log_entry)
        except Exception as e:
            logger.warning(f"Could not write to log file: {e}")
    
    def run(self):
        """Main execution flow - downloads both SNB and WSM"""
        try:
            logger.info("=" * 70)
            logger.info("VOS Image Downloader")
            logger.info("=" * 70)
            logger.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("")
            
            # Set paths
            self.set_version_path()
            self.set_directories()
            
            # Prepare directories
            self.create_directories()
            self.cleanup_old_files(self.snb_dir, "SNB")
            self.cleanup_old_files(self.wsm_dir, "WSM")
            
            success_count = 0
            downloaded_files = {}
            
            # Download Sandybridge version
            logger.info("")
            logger.info("=" * 70)
            logger.info("Downloading Sandybridge (SNB) version...")
            logger.info("=" * 70)
            
            snb_url = f"{self.base_url}/Sandybridge/"
            snb_pattern = r'^versa-flexvnf-.*J\.bin$'  # Ends with J.bin (NOT J-wsm.bin)
            
            snb_filename = self.manual_filename_snb if self.manual_filename_snb else self.get_matching_filename(snb_url, snb_pattern, "SNB")
            
            if snb_filename:
                snb_full_url = f"{snb_url}{snb_filename}"
                snb_output_path = os.path.join(self.snb_dir, snb_filename)
                
                if self.download_file(snb_full_url, snb_output_path, "SNB"):
                    success_count += 1
                    downloaded_files['snb'] = snb_filename
                    self.log_download('snb', snb_filename, snb_output_path)
            else:
                logger.error("✗ Failed to find SNB file")
            
            # Download Westmere version
            logger.info("")
            logger.info("=" * 70)
            logger.info("Downloading Westmere (WSM) version...")
            logger.info("=" * 70)
            
            wsm_url = f"{self.base_url}/Westmere/"
            wsm_pattern = r'^versa-flexvnf-.*J-wsm\.bin$'  # Ends with J-wsm.bin
            
            wsm_filename = self.manual_filename_wsm if self.manual_filename_wsm else self.get_matching_filename(wsm_url, wsm_pattern, "WSM")
            
            if wsm_filename:
                wsm_full_url = f"{wsm_url}{wsm_filename}"
                wsm_output_path = os.path.join(self.wsm_dir, wsm_filename)
                
                if self.download_file(wsm_full_url, wsm_output_path, "WSM"):
                    success_count += 1
                    downloaded_files['wsm'] = wsm_filename
                    self.log_download('wsm', wsm_filename, wsm_output_path)
            else:
                logger.error("✗ Failed to find WSM file")
            
            # Summary
            logger.info("")
            logger.info("=" * 70)
            logger.info("Download Summary")
            logger.info("=" * 70)
            logger.info(f"Successfully downloaded: {success_count}/2 files")
            
            if success_count == 2:
                logger.info("")
                logger.info("✓ All downloads completed successfully!")
                logger.info("")
                logger.info("Downloaded files:")
                logger.info(f"  SNB: {downloaded_files.get('snb', 'N/A')}")
                logger.info(f"  WSM: {downloaded_files.get('wsm', 'N/A')}")
                logger.info("")
                logger.info("File locations:")
                logger.info(f"  SNB: {self.snb_dir}")
                logger.info(f"  WSM: {self.wsm_dir}")
                return {
                    'status': 'success',
                    'downloaded': success_count,
                    'files': downloaded_files
                }
            elif success_count == 1:
                logger.warning("")
                logger.warning("⚠ Partial success - only 1 file downloaded")
                logger.warning("Please check the errors above")
                return {
                    'status': 'partial',
                    'downloaded': success_count,
                    'files': downloaded_files
                }
            else:
                logger.error("")
                logger.error("✗ All downloads failed")
                return {
                    'status': 'failed',
                    'downloaded': 0,
                    'files': {}
                }
        
        except Exception as e:
            logger.error("")
            logger.error("=" * 70)
            logger.error("✗ Download process failed")
            logger.error("=" * 70)
            logger.error(f"Error: {e}")
            return {
                'status': 'failed',
                'error': str(e)
            }


def main():
    parser = argparse.ArgumentParser(
        description='Download latest Versa FlexVNF images (both SNB and WSM)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Download latest 23.1.1 images (both architectures):
    %(prog)s 23.1.1

  Download with manual filenames:
    %(prog)s 23.1.1 --snb-filename versa-flexvnf-...-J.bin --wsm-filename versa-flexvnf-...-J-wsm.bin
        """
    )
    parser.add_argument(
        'build_version',
        help='Build version (e.g., 23.1.1)'
    )
    parser.add_argument(
        '--snb-filename',
        help='Manual SNB filename if auto-detection fails'
    )
    parser.add_argument(
        '--wsm-filename',
        help='Manual WSM filename if auto-detection fails'
    )
    
    args = parser.parse_args()
    
    downloader = ImageDownloader(args.build_version, args.snb_filename, args.wsm_filename)
    result = downloader.run()
    
    if result['status'] == 'failed':
        sys.exit(1)
    elif result['status'] == 'partial':
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()