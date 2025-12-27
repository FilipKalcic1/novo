#!/usr/bin/env python3
"""
BOT TRAINING & EVALUATION SCRIPT
================================
This script shows how the bot is trained and evaluates its precision.

Usage:
    python scripts/train_and_evaluate.py [--interactive]
"""

import asyncio
import json
import sys
import os
import io
from typing import List, Dict, Any, Tuple
from collections import defaultdict
import re

# Fix Windows console encoding for Croatian characters
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.registry import ToolRegistry
from services.patterns import READ_INTENT_PATTERNS, MUTATION_INTENT_PATTERNS


# ============================================================================
# TEST DATASET - Ground truth for evaluation
# ============================================================================
EVALUATION_DATASET = [
    # === AVAILABILITY ===
    {"query": "ima li slobodnih vozila", "expected_tool": "get_AvailableVehicles", "category": "availability"},
    {"query": "koja vozila su dostupna sutra", "expected_tool": "get_AvailableVehicles", "category": "availability"},
    {"query": "slobodna vozila za vikend", "expected_tool": "get_AvailableVehicles", "category": "availability"},
    {"query": "provjeri dostupnost vozila", "expected_tool": "get_AvailableVehicles", "category": "availability"},

    # === DAMAGE REPORTING ===
    {"query": "udario sam u stup", "expected_tool": "post_AddCase", "category": "damage"},
    {"query": "imam stetu na vozilu", "expected_tool": "post_AddCase", "category": "damage"},
    {"query": "ogrebao sam auto", "expected_tool": "post_AddCase", "category": "damage"},
    {"query": "prijavi kvar", "expected_tool": "post_AddCase", "category": "damage"},
    {"query": "problem s motorom", "expected_tool": "post_AddCase", "category": "damage"},

    # === VEHICLE INFO ===
    {"query": "koja je moja tablica", "expected_tool": "get_MasterData", "category": "vehicle_info"},
    {"query": "trenutna kilometraza", "expected_tool": "get_MasterData", "category": "vehicle_info"},
    {"query": "kad istice registracija", "expected_tool": "get_MasterData", "category": "vehicle_info"},
    {"query": "koliko do servisa", "expected_tool": "get_MasterData", "category": "vehicle_info"},
    {"query": "podaci o vozilu", "expected_tool": "get_MasterData", "category": "vehicle_info"},

    # === BOOKINGS ===
    {"query": "moje rezervacije", "expected_tool": "get_VehicleCalendar", "category": "booking"},
    {"query": "kad imam rezervaciju", "expected_tool": "get_VehicleCalendar", "category": "booking"},
    {"query": "pokazi moje bookinge", "expected_tool": "get_VehicleCalendar", "category": "booking"},

    # === MILEAGE ===
    {"query": "upisi kilometre 45000", "expected_tool": "post_AddMileage", "category": "mileage"},
    {"query": "unesi kilometrazu", "expected_tool": "post_AddMileage", "category": "mileage"},

    # === INFORMAL CROATIAN ===
    {"query": "di je moj auto", "expected_tool": "get_MasterData", "category": "informal"},
    {"query": "mos mi rec kad imam rezervaciju", "expected_tool": "get_VehicleCalendar", "category": "informal"},
    {"query": "daj mi podatke o autu", "expected_tool": "get_MasterData", "category": "informal"},
]


def print_header(text: str):
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def print_progress_bar(current: int, total: int, prefix: str = "", width: int = 40):
    """Print a progress bar."""
    percent = current / total
    filled = int(width * percent)
    bar = "#" * filled + "-" * (width - filled)
    print(f"\r{prefix} [{bar}] {current}/{total} ({percent*100:.0f}%)", end="", flush=True)


class BotTrainer:
    """Bot training and evaluation class."""

    def __init__(self):
        self.registry = None
        self.training_data = None
        self.results = []

    async def initialize(self):
        """Initialize the registry and load training data."""
        print("\n[1/3] Loading training data...")
        with open('data/training_queries.json', 'r', encoding='utf-8') as f:
            self.training_data = json.load(f)
        examples = self.training_data.get('examples', [])
        print(f"      Loaded {len(examples)} training examples")

        # Count by category
        categories = defaultdict(int)
        for ex in examples:
            categories[ex.get('category', 'unknown')] += 1
        print(f"      Categories: {len(categories)}")

        print("\n[2/3] Initializing Tool Registry...")
        self.registry = ToolRegistry()

        print("\n[3/3] Loading tools and embeddings...")
        # Load from settings
        from config import get_settings
        settings = get_settings()
        await self.registry.initialize(settings.swagger_sources)
        print(f"      Loaded {len(self.registry.tools)} tools")
        print(f"      Ready for evaluation!")

    def analyze_training_coverage(self):
        """Analyze how well training data covers different scenarios."""
        print_header("TRAINING DATA ANALYSIS")

        examples = self.training_data.get('examples', [])

        # Group by category
        by_category = defaultdict(list)
        for ex in examples:
            by_category[ex.get('category', 'unknown')].append(ex)

        # Group by primary tool
        by_tool = defaultdict(list)
        for ex in examples:
            by_tool[ex.get('primary_tool', 'unknown')].append(ex)

        print(f"\nTotal training examples: {len(examples)}")
        print(f"Unique categories: {len(by_category)}")
        print(f"Unique tools covered: {len(by_tool)}")

        print("\nTop 10 categories by examples:")
        sorted_cats = sorted(by_category.items(), key=lambda x: len(x[1]), reverse=True)
        for cat, exs in sorted_cats[:10]:
            print(f"  {cat:40} {len(exs):4} examples")

        print("\nTop 10 tools by training coverage:")
        sorted_tools = sorted(by_tool.items(), key=lambda x: len(x[1]), reverse=True)
        for tool, exs in sorted_tools[:10]:
            print(f"  {tool:40} {len(exs):4} examples")

        # Check for tools with NO training examples
        all_tools = set(self.registry.tools.keys())
        trained_tools = set(by_tool.keys())
        untrained = all_tools - trained_tools

        print(f"\nTools WITHOUT training examples: {len(untrained)}/{len(all_tools)}")
        if len(untrained) < 20:
            for tool in list(untrained)[:10]:
                print(f"  - {tool}")

    def show_training_matching(self, query: str, top_k: int = 5):
        """Show how training matching works for a query."""
        print(f"\nQuery: \"{query}\"")
        print("-" * 50)

        query_words = set(query.lower().split())
        matches = []

        for ex in self.training_data.get('examples', []):
            ex_query = ex.get('query', '')
            ex_words = set(ex_query.lower().split())
            overlap = query_words & ex_words

            if len(overlap) >= 1:
                matches.append({
                    'query': ex_query,
                    'tool': ex.get('primary_tool'),
                    'overlap': len(overlap),
                    'words': overlap
                })

        # Sort by overlap
        matches.sort(key=lambda x: x['overlap'], reverse=True)

        print(f"Found {len(matches)} training matches:")
        for i, m in enumerate(matches[:top_k]):
            print(f"  {i+1}. [{m['overlap']} words] \"{m['query'][:45]}...\"")
            print(f"     Tool: {m['tool']}")
            print(f"     Matching words: {m['words']}")

        if matches:
            print(f"\n  --> Training boost will be applied to: {matches[0]['tool']}")
        else:
            print(f"\n  --> No training match, relying on semantic search only")

    async def evaluate_precision(self, verbose: bool = True) -> Dict[str, Any]:
        """Evaluate bot precision on test dataset."""
        print_header("PRECISION EVALUATION")

        results = {
            'total': len(EVALUATION_DATASET),
            'correct_top1': 0,
            'correct_top3': 0,
            'correct_top5': 0,
            'by_category': defaultdict(lambda: {'total': 0, 'correct': 0}),
            'failures': []
        }

        print(f"\nEvaluating {len(EVALUATION_DATASET)} test queries...")
        print()

        for i, test in enumerate(EVALUATION_DATASET):
            query = test['query']
            expected = test['expected_tool']
            category = test['category']

            # Get predictions
            predictions = await self.registry.find_relevant_tools_with_scores(
                query=query,
                top_k=5,
                use_filtered_search=True
            )

            predicted_tools = [p['name'] for p in predictions]
            scores = [p['score'] for p in predictions]

            # Check accuracy
            top1_correct = expected in predicted_tools[:1]
            top3_correct = expected in predicted_tools[:3]
            top5_correct = expected in predicted_tools[:5]

            if top1_correct:
                results['correct_top1'] += 1
            if top3_correct:
                results['correct_top3'] += 1
            if top5_correct:
                results['correct_top5'] += 1

            results['by_category'][category]['total'] += 1
            if top1_correct:
                results['by_category'][category]['correct'] += 1

            # Track failures
            if not top1_correct:
                results['failures'].append({
                    'query': query,
                    'expected': expected,
                    'predicted': predicted_tools[:3],
                    'scores': scores[:3]
                })

            # Print progress
            status = "OK" if top1_correct else "MISS"
            if verbose:
                print(f"[{status:4}] {query[:40]:40} | Expected: {expected[:25]}")
                if not top1_correct:
                    print(f"       Got: {predicted_tools[0] if predicted_tools else 'None'}")
            else:
                print_progress_bar(i + 1, len(EVALUATION_DATASET), "Progress")

        if not verbose:
            print()  # New line after progress bar

        # Calculate metrics
        results['precision_top1'] = results['correct_top1'] / results['total']
        results['precision_top3'] = results['correct_top3'] / results['total']
        results['precision_top5'] = results['correct_top5'] / results['total']

        return results

    def print_results(self, results: Dict[str, Any]):
        """Print evaluation results."""
        print_header("EVALUATION RESULTS")

        print(f"""
    +---------------------------------------------+
    |           PRECISION METRICS                 |
    +---------------------------------------------+
    |  Top-1 Accuracy:  {results['precision_top1']*100:5.1f}%  ({results['correct_top1']}/{results['total']})      |
    |  Top-3 Accuracy:  {results['precision_top3']*100:5.1f}%  ({results['correct_top3']}/{results['total']})      |
    |  Top-5 Accuracy:  {results['precision_top5']*100:5.1f}%  ({results['correct_top5']}/{results['total']})      |
    +---------------------------------------------+
        """)

        print("\nAccuracy by Category:")
        print("-" * 50)
        for cat, stats in sorted(results['by_category'].items()):
            acc = stats['correct'] / stats['total'] * 100 if stats['total'] > 0 else 0
            bar = "#" * int(acc / 5) + "-" * (20 - int(acc / 5))
            print(f"  {cat:15} [{bar}] {acc:5.1f}% ({stats['correct']}/{stats['total']})")

        if results['failures']:
            print(f"\nFailures ({len(results['failures'])}):")
            print("-" * 50)
            for f in results['failures'][:5]:
                print(f"  Query: \"{f['query']}\"")
                print(f"    Expected: {f['expected']}")
                print(f"    Got: {f['predicted'][0]} (score: {f['scores'][0]:.3f})")
                print()

    async def interactive_mode(self):
        """Interactive testing mode."""
        print_header("INTERACTIVE TESTING MODE")
        print("Type a query to see how the bot processes it.")
        print("Commands: 'quit' to exit, 'train' to add training example")
        print()

        while True:
            try:
                query = input("\n> Enter query: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not query:
                continue
            if query.lower() == 'quit':
                break
            if query.lower() == 'train':
                await self.add_training_example()
                continue

            # Show intent detection
            print("\n[1] INTENT DETECTION")
            query_lower = query.lower()
            is_write = any(re.search(p, query_lower) for p in MUTATION_INTENT_PATTERNS)
            is_read = any(re.search(p, query_lower) for p in READ_INTENT_PATTERNS)
            intent = "WRITE" if is_write else ("READ" if is_read else "UNKNOWN")
            print(f"    Detected intent: {intent}")

            # Show training matching
            print("\n[2] TRAINING MATCHING")
            self.show_training_matching(query, top_k=3)

            # Show semantic search results
            print("\n[3] SEMANTIC SEARCH RESULTS")
            predictions = await self.registry.find_relevant_tools_with_scores(
                query=query,
                top_k=5,
                use_filtered_search=True
            )

            for i, p in enumerate(predictions):
                print(f"    {i+1}. {p['name']:40} (score: {p['score']:.3f})")

            print("\n[4] FINAL RECOMMENDATION")
            if predictions:
                print(f"    --> Use tool: {predictions[0]['name']}")
            else:
                print(f"    --> No matching tool found")

    async def add_training_example(self):
        """Add a new training example interactively."""
        print("\n--- ADD TRAINING EXAMPLE ---")

        query = input("Query: ").strip()
        if not query:
            return

        # Show available tools
        predictions = await self.registry.find_relevant_tools_with_scores(query, top_k=10)
        print("\nSuggested tools:")
        for i, p in enumerate(predictions[:5]):
            print(f"  {i+1}. {p['name']}")

        tool = input("\nPrimary tool (or number): ").strip()
        if tool.isdigit() and 1 <= int(tool) <= len(predictions):
            tool = predictions[int(tool)-1]['name']

        category = input("Category: ").strip()

        # Create example
        new_example = {
            "query": query,
            "intent": "USER_ADDED",
            "primary_tool": tool,
            "alternative_tools": [],
            "extract_fields": [],
            "response_template": None,
            "category": category
        }

        # Add to training data
        self.training_data['examples'].append(new_example)

        # Save
        with open('data/training_queries.json', 'w', encoding='utf-8') as f:
            json.dump(self.training_data, f, ensure_ascii=False, indent=2)

        print(f"\nAdded training example!")
        print(f"Total examples now: {len(self.training_data['examples'])}")


async def main():
    """Main entry point."""
    print("""
    +===============================================================+
    |           BOT TRAINING & EVALUATION SYSTEM                    |
    +===============================================================+
    |  This script trains and evaluates the Fleet Management Bot    |
    |  using training_queries.json as the training dataset.         |
    +===============================================================+
    """)

    trainer = BotTrainer()

    try:
        await trainer.initialize()
    except Exception as e:
        print(f"\nError initializing: {e}")
        print("Make sure you're running from the project root directory.")
        return

    # Analyze training data
    trainer.analyze_training_coverage()

    # Show training matching examples
    print_header("TRAINING MATCHING DEMO")
    demo_queries = [
        "mos mi rec kad imam rezervaciju",
        "slobodna vozila za vikend",
        "udario sam u auto",
        "random query without training match",
    ]
    for q in demo_queries:
        trainer.show_training_matching(q, top_k=2)

    # Run evaluation
    results = await trainer.evaluate_precision(verbose=True)
    trainer.print_results(results)

    # Interactive mode?
    if len(sys.argv) > 1 and sys.argv[1] == '--interactive':
        await trainer.interactive_mode()
    else:
        print("\nTip: Run with --interactive for interactive testing mode")
        print("     python scripts/train_and_evaluate.py --interactive")


if __name__ == "__main__":
    asyncio.run(main())
