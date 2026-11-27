import logging
import os
from pathlib import Path
import statistics
import sys
import matplotlib
# Only use Agg backend when SHOW_CHART is not set
if os.environ.get("SHOW_CHART") != "1":
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pydantic import BaseModel, ConfigDict, Field, field_validator
import requests

from common import fetch_all_pages

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("assignment2_scores")

API_URL = "http://localhost:8000/students"
CHART_PATH = Path(__file__).resolve().parent / "scores_chart.png"


class Student(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    score: float = Field(ge=0.0, le=100.0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Student name cannot be empty.")
        return cleaned

    @field_validator("score", mode="before")
    @classmethod
    def coerce_score(cls, value: object) -> float:
        try:
            return float(value)
        except (ValueError, TypeError):
            raise ValueError(f"Score must be numeric, got {value!r}")


def fetch_students_data(url: str) -> list[dict]:
    """Fetch all pages of student records with retries and specific error handling."""
    try:
        return fetch_all_pages(url)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        # Specific requirement: Print guidance if mock server is down after retries
        print("mock server not running, start mock_scores_api.py first")
        sys.exit(1)
    except PermissionError as err:
        print(f"Authentication error: {err}")
        sys.exit(1)
    except Exception as err:
        logger.error("Failed to fetch students data: %s", err)
        sys.exit(1)


def validate_students(raw_records: list[dict]) -> list[Student]:
    """Filter and validate student records, warning on duplicates and logging bad data."""
    valid_students: list[Student] = []
    seen_names: set[str] = set()

    for idx, item in enumerate(raw_records, start=1):
        try:
            student = Student.model_validate(item)
            if student.name in seen_names:
                logger.warning("Duplicate student name found: %s", student.name)
            else:
                seen_names.add(student.name)
            valid_students.append(student)
        except Exception as err:
            reason = err.errors()[0]["msg"] if hasattr(err, "errors") else "invalid student data"
            logger.warning("Record %d skipped: %s", idx, reason)

    return valid_students


def compute_statistics(students: list[Student]) -> dict[str, float]:
    """Calculate average, median, highest, and lowest scores."""
    scores = [s.score for s in students]
    return {
        "average": round(statistics.mean(scores), 2),
        "median": round(statistics.median(scores), 2),
        "highest": round(max(scores), 2),
        "lowest": round(min(scores), 2),
    }


def print_summary(stats: dict[str, float], total_students: int) -> None:
    """Print score summary metrics."""

    print("STUDENT SCORE SUMMARY:")
    print(f"Total Valid Students: {total_students}")
    print(f"Average Score:        {stats['average']:.2f}")
    print(f"Median Score:         {stats['median']:.2f}")
    print(f"Highest Score:        {stats['highest']:.2f}")
    print(f"Lowest Score:         {stats['lowest']:.2f}")


def generate_bar_chart(students: list[Student], avg_score: float, output_path: Path) -> None:
    """Render and save a bar chart colored by performance relative to average."""
    sorted_students = sorted(students, key=lambda s: s.score)
    names = [s.name for s in sorted_students]
    scores = [s.score for s in sorted_students]
    positions = list(range(len(sorted_students)))

    # Green for above/equal average, red for below
    colors = ["green" if score >= avg_score else "red" for score in scores]

    fig, ax = plt.subplots(figsize=(10, 6))
    # Plot by numeric position so duplicate student names get separate bars
    bars = ax.bar(positions, scores, color=colors, edgecolor="black", linewidth=0.6)

    # Average benchmark line
    ax.axhline(
        avg_score,
        color="blue",
        linestyle="--",
        linewidth=1.5,
        label=f"Average ({avg_score:.2f})",
    )

    ax.set_ylim(0, 100)
    ax.set_xlabel("Student Name", fontweight="bold")
    ax.set_ylabel("Score", fontweight="bold")
    ax.set_title("Student Examination Scores", fontsize=14, fontweight="bold")
    ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
    ax.set_xticks(positions)
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.legend(loc="upper left")

    plt.tight_layout()

    plt.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.info("Saved scores chart to %s", output_path)

    if os.environ.get("SHOW_CHART") == "1":
        plt.show()


def main() -> None:
    logger.info("Fetching student scores from %s...", API_URL)
    raw_records = fetch_students_data(API_URL)

    if not raw_records:
        print("API returned an empty list. No student records available.")
        sys.exit(0)

    students = validate_students(raw_records)
    if not students:
        print("No valid student records found after validation.")
        sys.exit(0)

    stats = compute_statistics(students)
    print_summary(stats, len(students))
    generate_bar_chart(students, stats["average"], CHART_PATH)


if __name__ == "__main__":
    main()
