Param(
    [string]$Message = "docs: update README",
    [switch]$Force
)

try {
    Write-Output "Staging README.md..."
    git add README.md

    Write-Output "Committing with message: $Message"
    git commit -m "$Message" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Output "No changes to commit."
    }

    Write-Output "Pushing to origin main..."
    if ($Force.IsPresent) {
        git push --force origin main
    } else {
        git push origin main
    }

    Write-Output "Done."
} catch {
    Write-Error "An error occurred: $_"
    exit 1
}
