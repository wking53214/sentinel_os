"""
Load Test - Test live Iceberg API server

Sends concurrent requests to /process and /batch endpoints
Measures latency, throughput, error rates
"""

import requests
import time
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys

class LiveLoadTester:
    def __init__(self, base_url: str = "http://localhost:9090"):
        self.base_url = base_url
        self.results = {
            "health": [],
            "process": [],
            "batch": [],
            "errors": []
        }
    
    def test_health(self) -> bool:
        """Test health endpoint"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            if response.status_code == 200:
                return True
        except Exception as e:
            self.results["errors"].append(f"Health check failed: {e}")
        return False
    
    def test_process_call(self, call_id: int) -> dict:
        """Test single call processing"""
        call = {
            "sid": f"CA{call_id:06d}",
            "status": "completed" if call_id % 3 != 0 else "no-answer",
            "duration": 120 + (call_id % 100),
            "from": f"+161255{call_id:05d}",
            "to": "+billing"
        }
        
        start = time.time()
        try:
            response = requests.post(
                f"{self.base_url}/process",
                json=call,
                timeout=10
            )
            elapsed = time.time() - start
            
            if response.status_code == 200:
                self.results["process"].append(elapsed)
                return {"success": True, "latency": elapsed}
            else:
                self.results["errors"].append(f"Process failed: {response.status_code}")
                return {"success": False, "error": response.status_code}
        except Exception as e:
            self.results["errors"].append(f"Process exception: {e}")
            return {"success": False, "error": str(e)}
    
    def test_batch(self, batch_size: int = 10) -> dict:
        """Test batch processing"""
        calls = [
            {
                "sid": f"CAB{i:06d}",
                "status": "completed" if i % 3 != 0 else "abandoned",
                "duration": 120 + i,
                "from": f"+161255{i:05d}",
                "to": "+tech"
            }
            for i in range(batch_size)
        ]
        
        start = time.time()
        try:
            response = requests.post(
                f"{self.base_url}/batch",
                json={"calls": calls},
                timeout=30
            )
            elapsed = time.time() - start
            
            if response.status_code == 200:
                self.results["batch"].append(elapsed)
                return {"success": True, "latency": elapsed, "batch_size": batch_size}
            else:
                self.results["errors"].append(f"Batch failed: {response.status_code}")
                return {"success": False, "error": response.status_code}
        except Exception as e:
            self.results["errors"].append(f"Batch exception: {e}")
            return {"success": False, "error": str(e)}
    
    def test_metrics(self) -> bool:
        """Test metrics endpoint"""
        try:
            response = requests.get(f"{self.base_url}/metrics", timeout=5)
            if response.status_code == 200 and "iceberg_calls_total" in response.text:
                return True
        except Exception as e:
            self.results["errors"].append(f"Metrics check failed: {e}")
        return False
    
    def run_load_test(self, num_calls: int = 20, num_workers: int = 4):
        """Run concurrent load test"""
        print(f"\nRunning load test: {num_calls} calls with {num_workers} workers\n")
        
        # Test health first
        print("[1/3] Testing health endpoint...")
        if not self.test_health():
            print("❌ API server not responding. Start with: docker-compose -f docker-compose-prod.yml up")
            return False
        print("✓ API is healthy")
        
        # Concurrent process calls
        print(f"\n[2/3] Processing {num_calls} calls concurrently...")
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(self.test_process_call, i)
                for i in range(num_calls)
            ]
            
            for i, future in enumerate(as_completed(futures)):
                result = future.result()
                if i % 5 == 0:
                    print(f"  Processed: {i+1}/{num_calls}")
        
        # Test batch
        print(f"\n[3/3] Testing batch endpoint...")
        self.test_batch(batch_size=10)
        
        # Test metrics
        print(f"\nTesting metrics endpoint...")
        self.test_metrics()
        
        return True
    
    def print_results(self):
        """Print test results"""
        print("\n" + "="*70)
        print("LOAD TEST RESULTS")
        print("="*70)
        
        if self.results["process"]:
            latencies = self.results["process"]
            print(f"\nProcess Calls: {len(latencies)} successful")
            print(f"  Min latency: {min(latencies)*1000:.1f}ms")
            print(f"  Max latency: {max(latencies)*1000:.1f}ms")
            print(f"  Avg latency: {statistics.mean(latencies)*1000:.1f}ms")
            print(f"  Median latency: {statistics.median(latencies)*1000:.1f}ms")
            if len(latencies) > 1:
                print(f"  Std deviation: {statistics.stdev(latencies)*1000:.1f}ms")
        
        if self.results["batch"]:
            batch_latencies = self.results["batch"]
            print(f"\nBatch Calls: {len(batch_latencies)} successful")
            print(f"  Avg batch latency: {statistics.mean(batch_latencies)*1000:.1f}ms")
        
        if self.results["errors"]:
            print(f"\nErrors: {len(self.results['errors'])}")
            for error in self.results["errors"][:5]:
                print(f"  - {error}")
            if len(self.results["errors"]) > 5:
                print(f"  ... and {len(self.results['errors']) - 5} more")
        
        print("\n" + "="*70 + "\n")

def main():
    tester = LiveLoadTester()
    
    # Run load test
    success = tester.run_load_test(num_calls=20, num_workers=4)
    
    # Print results
    tester.print_results()
    
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
