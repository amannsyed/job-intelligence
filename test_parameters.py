from collections import defaultdict
from ats_scraper import (
    detect_ats,
    scrape_greenhouse,
    scrape_ashby,
    scrape_smartrecruiters,
    scrape_workable,
    scrape_lever,
    scrape_personio,
    scrape_charliehr,
    scrape_rippling,
    scrape_salesforce,
    scrape_teamtailor,
    scrape_apple,
    scrape_avature,
    scrape_generic,
    sort_jobs_by_posted,
)

URLS = [
    "https://job-boards.greenhouse.io/anthropic",
    "https://jobs.ashbyhq.com/trainline",
    "https://careers.smartrecruiters.com/Cint",
    "https://apply.workable.com/starling-bank",
    "https://jobs.lever.co/factor",
    "https://aventumhr.my.salesforce-sites.com/Recruit/fRecruit__ApplyJobList",
    "https://careers.smartrecruiters.com/Version1/",
    "https://apply.workable.com/valsoft-corp/",
    "https://whitespaceglobal.recruit.charliehr.com/careers",
    "https://jobs.ashbyhq.com/Abound",
    "https://tri.jobs.personio.com/",
    # "https://bloomberg.avature.net/careers",
    "https://bloomberg.avature.net/careers/SearchJobs/?1845=%5B162558%5D&1845_format=3996&listFilterMode=1&jobRecordsPerPage=12&jobOffset=24",
    "https://jobs.apple.com/en-us/search?location=united-kingdom-GBR",
    "https://job-boards.greenhouse.io/stackadapt",
    "https://www.skyscanner.net/jobs/current-jobs",
    "https://ats.rippling.com/pythian/jobs",
    "https://www.jenstencareers.co.uk/jobs",
    "https://careers.blacksheepcoffee.co.uk/en-GB",
    "https://careers.moodys.com/en/search-jobs",
    "https://ats.rippling.com/dyad/jobs",
    "https://jobs.ashbyhq.com/accurx",
]

EXPECTED_FIELDS = [
    "job_title",
    "company",
    "location",
    "job_type",
    "workplace_type",
    "posted",
    "source_url",
]

def run_scraper(url: str):
    ats = detect_ats(url)

    if ats == "greenhouse":
        jobs = scrape_greenhouse(url)
    elif ats == "ashby":
        jobs = scrape_ashby(url)
    elif ats == "smartrecruiters":
        jobs = scrape_smartrecruiters(url)
    elif ats == "workable":
        jobs = scrape_workable(url)
    elif ats == "lever":
        jobs = scrape_lever(url)
    elif ats == "personio":
        jobs = scrape_personio(url)
    elif ats == "charliehr":
        jobs = scrape_charliehr(url)
    elif ats == "rippling":
        jobs = scrape_rippling(url)
    elif ats == "salesforce":
        jobs = scrape_salesforce(url)
    elif ats == "teamtailor":
        jobs = scrape_teamtailor(url)
    elif ats == "apple":
        jobs = scrape_apple(url)
    elif ats == "avature":
        jobs = scrape_avature(url)
    else:
        jobs = scrape_generic(url)

    return ats, sort_jobs_by_posted(jobs)

def is_blank(v):
    return v is None or str(v).strip() == ""

def run_field_completeness_test():
    print(f"{'URL':<75} | {'ATS':<15} | {'JOBS':<6} | {'TITLE':<5} | {'COMP':<5} | {'LOC':<5} | {'TYPE':<5} | {'WORK':<5} | {'POST':<5} | {'URL_OK':<6}")
    print("-" * 150)

    global_missing = defaultdict(int)
    total_jobs = 0

    for url in URLS:
        try:
            ats, jobs = run_scraper(url)
            total_jobs += len(jobs)

            missing_counts = {field: 0 for field in EXPECTED_FIELDS}

            for job in jobs:
                for field in EXPECTED_FIELDS:
                    if is_blank(job.get(field)):
                        missing_counts[field] += 1

            print(
                f"{url[:73]:<75} | "
                f"{ats:<15} | "
                f"{len(jobs):<6} | "
                f"{missing_counts['job_title']:<5} | "
                f"{missing_counts['company']:<5} | "
                f"{missing_counts['location']:<5} | "
                f"{missing_counts['job_type']:<5} | "
                f"{missing_counts['workplace_type']:<5} | "
                f"{missing_counts['posted']:<5} | "
                f"{missing_counts['source_url']:<6}"
            )

            for field, count in missing_counts.items():
                global_missing[field] += count

            bad_samples = []
            for job in jobs:
                missing = [f for f in EXPECTED_FIELDS if is_blank(job.get(f))]
                if missing:
                    bad_samples.append({
                        "title": job.get("job_title", ""),
                        "missing": missing,
                        "url": job.get("source_url", ""),
                    })
                if len(bad_samples) == 3:
                    break

            if bad_samples:
                print("  Sample incomplete rows:")
                for sample in bad_samples:
                    print(f"    - {sample['title'][:60]} | missing={sample['missing']} | {sample['url'][:90]}")

        except Exception as e:
            print(f"{url[:73]:<75} | ERROR           | {str(e)[:60]}")

    print("-" * 150)
    print(f"Total jobs checked: {total_jobs}")
    print("Missing totals by field:")
    for field in EXPECTED_FIELDS:
        print(f"  - {field}: {global_missing[field]}")

if __name__ == "__main__":
    run_field_completeness_test()