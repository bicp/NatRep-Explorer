# Deploy NatRep Explorer to Google Cloud Run, reachable only by signed-in company Google accounts (IAP).
#
# Prerequisites: Google Cloud CLI installed and signed in (`gcloud auth login`),
# and the workbook at data\Global_Nat_Rep_and_Zip_Codes_Combined.xlsx.
#
# Usage:  .\deploy.ps1 -Project <gcp-project-id> [-Region europe-west1] [-Domain pureprofile.com]
# Re-run the same command after replacing the workbook to publish new data.

param(
    [Parameter(Mandatory = $true)][string]$Project,
    [string]$Region = "europe-west1",
    [string]$Service = "natrep-explorer",
    [string]$Domain = "pureprofile.com"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "data\Global_Nat_Rep_and_Zip_Codes_Combined.xlsx")) {
    throw "Put the workbook at data\Global_Nat_Rep_and_Zip_Codes_Combined.xlsx first."
}

function Run { & $args[0] $args[1..($args.Count - 1)]; if ($LASTEXITCODE -ne 0) { throw "Failed: $args" } }

Write-Host "Enabling required APIs..."
Run gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com iap.googleapis.com --project $Project

Write-Host "Building in Cloud Build and deploying (takes a few minutes)..."
Run gcloud beta run deploy $Service --source . --project $Project --region $Region `
    --port 8000 --memory 2Gi --cpu 1 --min-instances 0 --max-instances 3 `
    --no-allow-unauthenticated --iap --quiet

Write-Host "Letting IAP call the service..."
$num = (gcloud projects describe $Project --format "value(projectNumber)")
Run gcloud run services add-iam-policy-binding $Service --project $Project --region $Region `
    --member "serviceAccount:service-$num@gcp-sa-iap.iam.gserviceaccount.com" --role roles/run.invoker --quiet

Write-Host "Allowing everyone at $Domain to sign in..."
Run gcloud beta iap web add-iam-policy-binding --resource-type cloud-run --service $Service `
    --project $Project --region $Region --member "domain:$Domain" --role roles/iap.httpsResourceAccessor --quiet

$url = (gcloud run services describe $Service --project $Project --region $Region --format "value(status.url)")
Write-Host "`nDone. Team link: $url"
