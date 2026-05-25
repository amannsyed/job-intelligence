import sys
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

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
    "https://jobs.ashbyhq.com/accurx"
]

def run_tests():
    print(f"{'URL':<75} | {'ATS DETECTED':<15} | {'JOBS FOUND'}")
    print("-" * 110)
    
    total_jobs = 0
    for url in URLS:
        try:
            resp = client.post("/api/scrape-ats", json={"url": url})
            if resp.status_code == 200:
                data = resp.json()
                if data.get("jobs"):
                    print(data["jobs"][0])
                ats = data.get("ats_detected", "error")
                jobs_count = len(data.get("jobs", []))
                total_jobs += jobs_count
                print(f"{url[:73]:<75} | {ats:<15} | {jobs_count}")
            else:
                print(f"{url[:73]:<75} | {'HTTP '+str(resp.status_code):<15} | ERROR")
        except Exception as e:
            print(f"{url[:73]:<75} | {'EXCEPTION':<15} | {str(e)[:20]}")
            
    print("-" * 110)
    print(f"Total jobs extracted: {total_jobs}")

if __name__ == "__main__":
    run_tests()
