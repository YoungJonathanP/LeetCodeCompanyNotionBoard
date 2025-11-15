#!/usr/bin/env python3
"""
Topic-based analysis for LeetCode company questions.
Analyzes topic tag distributions across companies and generates interactive visualizations.
"""

import os
import sys
import json
import argparse
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime
from collections import defaultdict

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import chi2_contingency
from scipy import stats
from dotenv import load_dotenv

# ---------------------------
# Configuration
# ---------------------------

# Topics to exclude from analysis (too generic)
EXCLUDED_TOPICS = {"Array", "String", "Tree"}

# Visualization config
VIZ_CONFIG = {
    'height': 1200,
    'font_size': 14,
    'margin': dict(l=150, r=100, t=150, b=150),
    'combined_height': 1400,
    'combined_margin': dict(l=300, r=100, t=200, b=150)
}

# Auto-open HTML files in browser
AUTO_OPEN_HTML = False

COMPANIES_ROOT = os.getenv("COMPANIES_ROOT", "companies")

# ---------------------------
# Utilities
# ---------------------------

def log(msg: str):
    print(msg, file=sys.stdout)

def warn(msg: str):
    print(f"[WARN] {msg}", file=sys.stderr)

def filter_topics(topics_list: List[str]) -> List[str]:
    """Filter out excluded topics"""
    return [topic for topic in topics_list if topic not in EXCLUDED_TOPICS]

def normalize_title(t: str) -> str:
    return " ".join(t.strip().lower().split())

# ---------------------------
# Data Loading
# ---------------------------

def load_company_snapshot(company_name: str, date_folder: Optional[str] = None) -> Optional[Dict[str, List[dict]]]:
    """
    Load snapshot data for a company from JSON files.
    Returns dict with keys: '30d', '90d', '180d' -> list of questions
    """
    company_dir = Path(COMPANIES_ROOT) / company_name

    if not company_dir.exists():
        warn(f"Company directory not found: {company_dir}")
        return None

    if date_folder:
        snapshot_dir = company_dir / date_folder
    else:
        # Get latest date folder
        dated = sorted([d for d in company_dir.iterdir() if d.is_dir()],
                      key=lambda p: p.name, reverse=True)
        if not dated:
            warn(f"No date folders found in {company_dir}")
            return None
        snapshot_dir = dated[0]

    if not snapshot_dir.exists():
        warn(f"Snapshot directory not found: {snapshot_dir}")
        return None

    log(f"Loading snapshot from: {snapshot_dir}")

    snapshots = {}
    for window in ['30d', '90d', '180d']:
        json_file = snapshot_dir / f"{window}.json"
        if not json_file.exists():
            warn(f"Missing {window}.json in {snapshot_dir}")
            continue

        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Handle nested structure: data.favoriteQuestionList.questions
                if isinstance(data, dict) and 'data' in data:
                    questions_data = data.get('data', {}).get('favoriteQuestionList', {}).get('questions', [])
                    snapshots[window] = questions_data
                else:
                    # Assume it's already a list of questions
                    snapshots[window] = data if isinstance(data, list) else []
        except Exception as e:
            warn(f"Error loading {json_file}: {e}")

    return snapshots if snapshots else None

def extract_topics_from_snapshots(snapshots: Dict[str, List[dict]],
                                 window: str = '180d') -> Tuple[Dict[str, float], Dict[str, List[str]]]:
    """
    Extract topic frequencies from snapshot data.
    Returns: (topic_weights, topic_questions_map)
    - topic_weights: dict of topic -> weighted frequency
    - topic_questions_map: dict of topic -> list of question titles
    """
    if window not in snapshots:
        warn(f"Window {window} not found in snapshots")
        return {}, {}

    questions = snapshots[window]
    topic_weights = defaultdict(float)
    topic_questions = defaultdict(list)

    for q in questions:
        # Get frequency (use as weight)
        frequency = q.get('frequency', 1.0)
        if frequency is None:
            frequency = 1.0

        # Get topic tags - handle both string and list of dicts
        tags_raw = q.get('topicTags', [])
        tags = []
        if isinstance(tags_raw, str):
            # If tags is a string, split by comma
            tags = [t.strip() for t in tags_raw.split(',')]
        elif isinstance(tags_raw, list):
            # If tags is a list of dicts (GraphQL format), extract 'name' field
            for tag_item in tags_raw:
                if isinstance(tag_item, dict) and 'name' in tag_item:
                    tags.append(tag_item['name'])
                elif isinstance(tag_item, str):
                    tags.append(tag_item)

        # Get title for mapping
        title = q.get('title', 'Untitled')
        frontend_id = q.get('questionFrontendId', '')
        display_title = f"{frontend_id}. {title}" if frontend_id else title

        # Filter and count topics
        filtered_tags = filter_topics(tags)
        for tag in filtered_tags:
            topic_weights[tag] += frequency
            if display_title not in topic_questions[tag]:
                topic_questions[tag].append(display_title)

    return dict(topic_weights), dict(topic_questions)

# ---------------------------
# Statistical Analysis
# ---------------------------

def compare_distributions(dist1: pd.Series, dist2: pd.Series,
                         name1: str, name2: str) -> dict:
    """Compare two topic distributions using statistical tests"""

    # Get all unique topics
    all_topics = set(dist1.index) | set(dist2.index)

    # Create aligned arrays
    aligned_dist1 = []
    aligned_dist2 = []

    for topic in sorted(all_topics):
        aligned_dist1.append(dist1.get(topic, 0))
        aligned_dist2.append(dist2.get(topic, 0))

    aligned_dist1 = np.array(aligned_dist1)
    aligned_dist2 = np.array(aligned_dist2)

    results = {
        'comparison': f"{name1} vs {name2}",
        'topics_compared': len(all_topics),
        'total_problems_1': aligned_dist1.sum(),
        'total_problems_2': aligned_dist2.sum()
    }

    # Chi-square test
    try:
        contingency_table = np.array([
            np.round(aligned_dist1).astype(int),
            np.round(aligned_dist2).astype(int)
        ])
        chi2_stat, chi2_p, dof, expected = chi2_contingency(contingency_table)
        results['chi2_statistic'] = chi2_stat
        results['chi2_p_value'] = chi2_p
        results['chi2_dof'] = dof
        results['chi2_significant'] = chi2_p < 0.05

        # Cramér's V effect size
        n = contingency_table.sum()
        cramers_v = np.sqrt(chi2_stat / (n * (min(contingency_table.shape) - 1)))
        results['cramers_v'] = cramers_v
    except Exception as e:
        results['chi2_error'] = str(e)

    # G-test
    try:
        total1, total2 = aligned_dist1.sum(), aligned_dist2.sum()
        total_combined = total1 + total2

        if total_combined > 0:
            g_stat = 0
            valid_categories = 0

            for i in range(len(aligned_dist1)):
                o1, o2 = aligned_dist1[i], aligned_dist2[i]
                if o1 + o2 > 0:
                    expected_total = o1 + o2
                    e1 = expected_total * total1 / total_combined
                    e2 = expected_total * total2 / total_combined

                    if o1 > 0:
                        g_stat += 2 * o1 * np.log(o1 / e1)
                    if o2 > 0:
                        g_stat += 2 * o2 * np.log(o2 / e2)
                    valid_categories += 1

            dof = valid_categories - 1
            if dof > 0:
                g_p = 1 - stats.chi2.cdf(g_stat, dof)
                results['g_statistic'] = g_stat
                results['g_p_value'] = g_p
                results['g_dof'] = dof
                results['g_significant'] = g_p < 0.05
    except Exception as e:
        results['g_error'] = str(e)

    # Top differences
    diff_analysis = []
    for topic in sorted(all_topics):
        count1 = dist1.get(topic, 0)
        count2 = dist2.get(topic, 0)
        prop1 = count1 / results['total_problems_1'] if results['total_problems_1'] > 0 else 0
        prop2 = count2 / results['total_problems_2'] if results['total_problems_2'] > 0 else 0
        diff = prop1 - prop2

        diff_analysis.append({
            'topic': topic,
            f'{name1}_count': count1,
            f'{name2}_count': count2,
            f'{name1}_proportion': prop1,
            f'{name2}_proportion': prop2,
            'difference': diff,
            'abs_difference': abs(diff)
        })

    diff_analysis.sort(key=lambda x: x['abs_difference'], reverse=True)
    results['top_differences'] = diff_analysis[:10]

    return results

def save_statistical_results(results_list: List[dict],
                            output_dir: str = "topic_analysis_output"):
    """Save statistical comparison results to text file"""

    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    filename = output_path / "statistical_comparison_results.txt"

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("STATISTICAL COMPARISON OF TOPIC DISTRIBUTIONS\n")
        f.write("=" * 80 + "\n")
        f.write(f"Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        f.write("SUMMARY:\n")
        f.write("Chi-square test: tests if distributions are significantly different\n")
        f.write("G-test: alternative test for categorical data independence\n")
        f.write("Cramer's V: effect size (0=no association, 1=perfect association)\n\n")

        for i, result in enumerate(results_list, 1):
            f.write(f"COMPARISON {i}: {result['comparison']}\n")
            f.write("-" * 50 + "\n")

            f.write(f"Total problems: {result['total_problems_1']:.1f} vs {result['total_problems_2']:.1f}\n")
            f.write(f"Topics analyzed: {result['topics_compared']}\n\n")

            if 'chi2_statistic' in result:
                f.write(f"CHI-SQUARE TEST:\n")
                f.write(f"  Statistic: {result['chi2_statistic']:.4f}\n")
                f.write(f"  P-value: {result['chi2_p_value']:.2e}\n")
                f.write(f"  Significant: {'YES' if result['chi2_significant'] else 'NO'} (α=0.05)\n")
                if result.get('cramers_v'):
                    f.write(f"  Cramer's V: {result['cramers_v']:.4f}\n")
                f.write("\n")

            if 'g_statistic' in result:
                f.write(f"G-TEST:\n")
                f.write(f"  Statistic: {result['g_statistic']:.4f}\n")
                f.write(f"  P-value: {result['g_p_value']:.2e}\n")
                f.write(f"  Significant: {'YES' if result['g_significant'] else 'NO'} (α=0.05)\n\n")

            f.write(f"TOP 10 TOPIC DIFFERENCES:\n")
            names = result['comparison'].split(' vs ')
            f.write(f"{'Rank':<6} {'Topic':<25} {names[0]:<12} {names[1]:<12} {'Diff':<10}\n")
            f.write("-" * 80 + "\n")

            for rank, diff in enumerate(result['top_differences'], 1):
                name1, name2 = names
                prop1 = diff[f'{name1}_proportion']
                prop2 = diff[f'{name2}_proportion']
                f.write(f"{rank:<6} {diff['topic']:<25} {prop1:<12.3f} {prop2:<12.3f} {diff['difference']:+.3f}\n")

            f.write("\n" + "=" * 80 + "\n\n")

    log(f"Statistical results saved to: {filename}")

# ---------------------------
# Visualization
# ---------------------------

def create_interactive_visualizations(topic_counts: pd.Series,
                                     topic_percentages: pd.Series,
                                     company_name: str,
                                     window: str,
                                     topic_questions: Optional[Dict[str, List[str]]] = None,
                                     output_dir: str = "topic_analysis_output"):
    """Create interactive HTML visualizations using Plotly"""

    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    # Create subplots
    fig = make_subplots(
        rows=3, cols=1,
        subplot_titles=(
            'Top 10 Topics Distribution',
            'Top 15 Topics (Hover for Questions)',
            'All Topics Frequency Table'
        ),
        specs=[
            [{"type": "pie"}],
            [{"type": "bar"}],
            [{"type": "table"}]
        ],
        vertical_spacing=0.15
    )

    # 1. Pie chart - top 10
    top_10_counts = topic_counts.head(10)
    top_10_percentages = topic_percentages.head(10)

    hover_template = '<b>%{label}</b><br>Count: %{value}<br>Percentage: %{percent}'
    if topic_questions:
        customdata = []
        for topic in top_10_counts.index:
            questions = topic_questions.get(topic, [])
            if questions:
                question_list = questions[:10]
                questions_text = '<br>'.join([f"• {q}" for q in question_list])
                if len(questions) > 10:
                    questions_text += f"<br>... and {len(questions) - 10} more"
                customdata.append(questions_text)
            else:
                customdata.append("No questions found")
        hover_template += '<br><br>Questions:<br>%{customdata}<extra></extra>'

        fig.add_trace(
            go.Pie(
                labels=top_10_counts.index,
                values=top_10_counts.values,
                name="Topic Distribution",
                hovertemplate=hover_template,
                textinfo='label+percent',
                customdata=customdata
            ),
            row=1, col=1
        )
    else:
        hover_template += '<extra></extra>'
        fig.add_trace(
            go.Pie(
                labels=top_10_counts.index,
                values=top_10_counts.values,
                name="Topic Distribution",
                hovertemplate=hover_template,
                textinfo='label+percent'
            ),
            row=1, col=1
        )

    # 2. Bar chart - top 15
    top_15_counts = topic_counts.head(15)
    top_15_percentages = topic_percentages.head(15)

    hover_template = '<b>%{x}</b><br>Count: %{y}<br>Percentage: %{customdata}'
    if topic_questions:
        bar_customdata = []
        for topic in top_15_counts.index:
            questions = topic_questions.get(topic, [])
            percentage = f'{topic_percentages[topic]}%'
            if questions:
                question_list = questions[:8]
                questions_text = '<br>'.join([f"• {q}" for q in question_list])
                if len(questions) > 8:
                    questions_text += f"<br>... and {len(questions) - 8} more"
                combined_data = f"{percentage}<br><br>Questions:<br>{questions_text}"
            else:
                combined_data = f"{percentage}<br><br>No questions found"
            bar_customdata.append(combined_data)
        hover_template += '<extra></extra>'

        fig.add_trace(
            go.Bar(
                x=top_15_counts.index,
                y=top_15_counts.values,
                name='Top 15 Topics',
                marker_color='steelblue',
                text=[f'{count:.1f}<br>({perc}%)' for count, perc in zip(top_15_counts.values, top_15_percentages.values)],
                textposition='outside',
                hovertemplate=hover_template,
                customdata=bar_customdata,
                showlegend=False
            ),
            row=2, col=1
        )
    else:
        hover_template += '<extra></extra>'
        fig.add_trace(
            go.Bar(
                x=top_15_counts.index,
                y=top_15_counts.values,
                name='Top 15 Topics',
                marker_color='steelblue',
                text=[f'{count:.1f}<br>({perc}%)' for count, perc in zip(top_15_counts.values, top_15_percentages.values)],
                textposition='outside',
                hovertemplate=hover_template,
                customdata=[f'{p}%' for p in top_15_percentages.values],
                showlegend=False
            ),
            row=2, col=1
        )

    # 3. Table - all topics
    ascending_counts = topic_counts.sort_values(ascending=True)
    ascending_percentages = topic_percentages[ascending_counts.index]
    ranks = list(range(1, len(ascending_counts) + 1))

    if topic_questions:
        question_samples = []
        for topic in ascending_counts.index:
            questions = topic_questions.get(topic, [])
            if questions:
                sample = questions[:3]
                sample_text = '; '.join(sample)
                if len(questions) > 3:
                    sample_text += f' (+{len(questions) - 3} more)'
                question_samples.append(sample_text)
            else:
                question_samples.append('No questions found')

        fig.add_trace(
            go.Table(
                header=dict(
                    values=['Rank', 'Topic', 'Count', 'Percentage', 'Sample Questions'],
                    fill_color='lightblue',
                    align='left',
                    font=dict(size=12, color='black')
                ),
                cells=dict(
                    values=[
                        ranks,
                        ascending_counts.index.tolist(),
                        [f'{val:.1f}' for val in ascending_counts.values.tolist()],
                        [f'{p}%' for p in ascending_percentages.values],
                        question_samples
                    ],
                    fill_color='white',
                    align='left',
                    font=dict(size=11, color='black'),
                    height=25
                )
            ),
            row=3, col=1
        )
    else:
        fig.add_trace(
            go.Table(
                header=dict(
                    values=['Rank', 'Topic', 'Count', 'Percentage'],
                    fill_color='lightblue',
                    align='left',
                    font=dict(size=12, color='black')
                ),
                cells=dict(
                    values=[
                        ranks,
                        ascending_counts.index.tolist(),
                        [f'{val:.1f}' for val in ascending_counts.values.tolist()],
                        [f'{p}%' for p in ascending_percentages.values]
                    ],
                    fill_color='white',
                    align='left',
                    font=dict(size=11, color='black'),
                    height=25
                )
            ),
            row=3, col=1
        )

    # Update layout
    title = f'{company_name} - {window} - Topic Analysis'
    fig.update_layout(
        title=title,
        title_x=0.5,
        height=2000,
        showlegend=False,
        font=dict(size=VIZ_CONFIG['font_size']),
        margin=dict(l=150, r=100, t=200, b=250)
    )

    fig.update_xaxes(title_text="Topics", row=2, col=1, tickangle=45)
    fig.update_yaxes(title_text="Count", row=2, col=1)

    # Save HTML
    html_filename = output_path / f'{company_name}_{window}_topic_analysis.html'
    fig.write_html(html_filename)
    log(f"Saved visualization: {html_filename}")

    if AUTO_OPEN_HTML:
        webbrowser.open('file://' + str(html_filename.absolute()))

# ---------------------------
# Analysis Functions
# ---------------------------

def analyze_single_company(company_name: str,
                          window: str = '180d',
                          date_folder: Optional[str] = None,
                          output_dir: str = "topic_analysis_output") -> Optional[Tuple[pd.Series, pd.Series]]:
    """Analyze topic distribution for a single company"""

    log(f"\n{'='*60}")
    log(f"Analyzing: {company_name} ({window})")
    log(f"{'='*60}")

    snapshots = load_company_snapshot(company_name, date_folder)
    if not snapshots:
        warn(f"Could not load snapshots for {company_name}")
        return None

    topic_weights, topic_questions = extract_topics_from_snapshots(snapshots, window)

    if not topic_weights:
        warn(f"No topics found for {company_name}")
        return None

    # Convert to pandas Series
    topic_counts = pd.Series(topic_weights).sort_values(ascending=False)
    total_weighted = topic_counts.sum()
    topic_percentages = (topic_counts / total_weighted * 100).round(1)

    log(f"\nTotal weighted topic occurrences: {total_weighted:.1f}")
    log(f"Unique topics (after filtering): {len(topic_counts)}")
    log(f"\nTop 15 Topics:")
    for topic, count in topic_counts.head(15).items():
        percentage = topic_percentages[topic]
        log(f"  {topic}: {count:.1f} ({percentage}%)")

    # Create visualization
    create_interactive_visualizations(
        topic_counts, topic_percentages,
        company_name, window, topic_questions, output_dir
    )

    return topic_counts, topic_percentages

def analyze_multiple_companies(company_names: List[str],
                               window: str = '180d',
                               combined_name: str = "Combined",
                               output_dir: str = "topic_analysis_output") -> Optional[Tuple[pd.Series, pd.Series]]:
    """Analyze combined topic distribution across multiple companies"""

    log(f"\n{'='*60}")
    log(f"Analyzing Combined: {', '.join(company_names)} ({window})")
    log(f"{'='*60}")

    all_topic_weights = defaultdict(float)
    all_topic_questions = defaultdict(set)

    for company_name in company_names:
        snapshots = load_company_snapshot(company_name)
        if not snapshots:
            warn(f"Skipping {company_name} - could not load snapshots")
            continue

        topic_weights, topic_questions = extract_topics_from_snapshots(snapshots, window)

        for topic, weight in topic_weights.items():
            all_topic_weights[topic] += weight

        for topic, questions in topic_questions.items():
            all_topic_questions[topic].update(questions)

    if not all_topic_weights:
        warn("No topics found across all companies")
        return None

    # Convert to pandas Series
    topic_counts = pd.Series(all_topic_weights).sort_values(ascending=False)
    total_weighted = topic_counts.sum()
    topic_percentages = (topic_counts / total_weighted * 100).round(1)

    # Convert sets to lists for visualization
    topic_questions_list = {k: list(v) for k, v in all_topic_questions.items()}

    log(f"\nTotal weighted topic occurrences: {total_weighted:.1f}")
    log(f"Unique topics (after filtering): {len(topic_counts)}")
    log(f"Companies analyzed: {len(company_names)}")
    log(f"\nTop 15 Topics:")
    for topic, count in topic_counts.head(15).items():
        percentage = topic_percentages[topic]
        log(f"  {topic}: {count:.1f} ({percentage}%)")

    # Create visualization
    create_interactive_visualizations(
        topic_counts, topic_percentages,
        combined_name, window, topic_questions_list, output_dir
    )

    return topic_counts, topic_percentages

# ---------------------------
# Main
# ---------------------------

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Analyze LeetCode question topic distributions by company"
    )
    parser.add_argument(
        "--company",
        type=str,
        help="Single company to analyze (e.g., 'Meta')"
    )
    parser.add_argument(
        "--companies",
        type=str,
        help="Comma-separated list of companies to analyze together (e.g., 'Meta,Google,Amazon')"
    )
    parser.add_argument(
        "--window",
        type=str,
        default="180d",
        choices=["30d", "90d", "180d"],
        help="Time window to analyze (default: 180d)"
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare all specified companies statistically"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="topic_analysis_output",
        help="Output directory for HTML files and results (default: topic_analysis_output)"
    )
    parser.add_argument(
        "--auto-open",
        action="store_true",
        help="Automatically open HTML visualizations in browser"
    )

    args = parser.parse_args()

    global AUTO_OPEN_HTML
    AUTO_OPEN_HTML = args.auto_open

    # Ensure output directory exists
    Path(args.output).mkdir(exist_ok=True)

    distributions = {}

    if args.company:
        # Single company analysis
        result = analyze_single_company(args.company, args.window, output_dir=args.output)
        if result:
            distributions[args.company] = result[0]

    elif args.companies:
        # Multiple companies
        company_list = [c.strip() for c in args.companies.split(',')]

        # Analyze each individually
        for company in company_list:
            result = analyze_single_company(company, args.window, output_dir=args.output)
            if result:
                distributions[company] = result[0]

        # Analyze combined
        if len(company_list) > 1:
            combined_result = analyze_multiple_companies(
                company_list, args.window,
                f"{'+'.join(company_list)}_Combined",
                output_dir=args.output
            )
            if combined_result:
                distributions[f"{'+'.join(company_list)}_Combined"] = combined_result[0]

    else:
        parser.print_help()
        sys.exit(1)

    # Statistical comparison
    if args.compare and len(distributions) >= 2:
        log(f"\n{'='*60}")
        log("Statistical Comparisons")
        log(f"{'='*60}")

        comparison_results = []
        company_names = list(distributions.keys())

        # Compare all pairs
        for i in range(len(company_names)):
            for j in range(i + 1, len(company_names)):
                name1, name2 = company_names[i], company_names[j]
                log(f"\nComparing: {name1} vs {name2}")
                result = compare_distributions(
                    distributions[name1], distributions[name2],
                    name1, name2
                )
                comparison_results.append(result)

                # Print quick summary
                if 'chi2_p_value' in result:
                    sig = "SIGNIFICANT" if result['chi2_significant'] else "not significant"
                    log(f"  Chi-square p-value: {result['chi2_p_value']:.4e} ({sig})")
                if 'cramers_v' in result:
                    log(f"  Effect size (Cramér's V): {result['cramers_v']:.4f}")

        if comparison_results:
            save_statistical_results(comparison_results, args.output)

    log(f"\n{'='*60}")
    log("Analysis Complete!")
    log(f"Output directory: {args.output}")
    log(f"{'='*60}")

if __name__ == "__main__":
    main()
