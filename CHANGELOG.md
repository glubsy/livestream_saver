# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.0.0] - 2026-06-15

### Added
- Support for using yt-dlp to discover livestream formats and download muxed HLS segment streams
- Configurable `use_ytdl` setting in the template configuration file to let users choose the downloader
- Support for resuming muxed HLS downloads from existing segment output directories
- Support for merging single-track muxed segment downloads without requiring an `aud/` directory
- Dedicated Docker Compose service for the external bgutil POT provider
- Podman-specific Compose override file using host networking
- Optional dependency group / requirements file for POT-related yt-dlp integration

### Changed
- BREAKING: Renamed `max_video_quality` to `max_video_width` in configuration handling
- Renamed the preferred CLI flag to `--max-video-width` while keeping `--max-video-quality` as a compatibility alias
- Updated yt-dlp probing so format filters such as `format` and `format_sort` are removed before stream discovery
- Reduced dependency on pytube metadata lookups in the yt-dlp/HLS download path
- Improved merge logging for valid partial-duration recordings
- Renamed extracted thumbnails to match the final output filename and correct image extension
- Moved the bgutil POT provider out of the downloader container image and into a separate service
- Moved POT-related Python package out of the default dependency set and into optional installation paths
- Updated Docker and installation documentation to describe the external POT provider requirement

### Fixed
- Fixed selection of the most appropriate muxed HLS format based on the configured maximum video width
- Fixed incorrect fallback to old YouTube API metadata calls during yt-dlp-driven HLS downloads
- Fixed noisy segment progress messages being written to download log files
- Fixed cookie path handling when paths contain `~`, including `_updated.txt` cookie jar writes
- Fixed Podman Compose progress display by disabling TTY in the Podman override
- Fixed muxed HLS resume behavior when playlist media sequence has already advanced

## [v1.0.1] - 2026-06-13

- Fix video Ids being wrongly detected as newly added to the channel
- Add deno as dependency from yt-dlp
- Add ejs as dependency from yt-dlp

## [v1.0.0] - 2026-06-13

- Remove PO token manual extraction requirements and replace with automated provider

## [v0.4.0] - 2026-06-11

- Fix retrieving videos from tabs

## [v0.3.3] - 2025-10-30

- Allow passing empty PO token value to avoid conflict with external PO token providers

## [v0.3.2] - 2025-10-25

### Added
- Store PO token in a file inside the configuration directory

## [v0.3.1] - 2025-08-24

### Added
- BREAKING: Support for loading PO_TOKEN from environment variables.
- BREAKING: Enhanced .env file loading capabilities. PO_TOKEN is now loaded from that file.
- BREAKING: SMTP related variables are now loaded from environment variables or .env file
- BREAKING: PO_TOKEN value must be loaded from .env file or environment variable
- Auto upgrade of yt-dlp on container start to use latest version from PyPI

### Changed
- Updated pyproject.toml and dependencies for uv package manager
- Replaced obsolete Docker Compose examples
- Renamed Dockerfile to Containerfile (more generic container name)
- Updated Docker/container configuration
- Updated README documentation and template config file
- Replaced stale example channels in configuration templates
- Enhanced logger configuration for better debugging

### Fixed
- Fixed .env file loading mechanism (only load variables not already present in environment)
- Minor bug fixes and improvements
- Fixed log level reset issues after calling yt-dlp

---

## [v0.2.2] - 2025-04-19

### Changed
- Upgraded Docker Python version
- Replaced deprecated `imghdr` library with `filetype` library

### Fixed
- Compatibility improvements for newer Python versions

---

## [v0.2.1] - 2024-10-13

### Added
- Extractor arguments support to pass PO token to yt-dlp
- Unit tests for `warn_of_new` and `get_changes` functions
- Endpoints as class property

### Changed
- Updated pyproject.toml configuration

### Removed
- Obsolete monitor function
- Temporary patches and unused API key references

---

## [v0.2.0] - 2024-07-21

### Added
- Support for multiple simultaneous live streams
- `max-simultaneous-streams` parameter for controlling concurrent downloads
- Channel name storage in Video post objects
- MissingVideoId exception handling

### Changed
- Updated user agent string
- Improved session object initialization
- Enhanced filename template in yt-dlp default configuration
- Renamed monitor module for better organization

### Fixed
- Deprecated datetime method usage
- WaitingException handling
- Video ID deduplication when fetching live videos

---

## [v0.1.1] - 2024-05-05

### Fixed
- Fixed passing no cookies path to downloader
- Fixed warnings when detecting possible missing video IDs
- Improved error handling for missing video detection

---

## [0.1.0] - 2024-02-17

### Added
- Docker Hub public image reference in README
- Enhanced configuration handling

### Fixed
- ConfigParser now properly handles string values
- Various configuration-related improvements

---

## [0.0.1] - Initial Release

### Added
- Initial project setup and core functionality
- YouTube livestream monitoring and downloading capabilities
- Channel monitoring for upcoming livestreams
- Cookie support for membership-only and age-restricted videos
- Configuration file support
- Basic Docker support

### Features
- Download YouTube livestreams from beginning to end
- Automatic monitoring of channels for new livestreams
- Support for yt-dlp integration
- Configurable download settings
