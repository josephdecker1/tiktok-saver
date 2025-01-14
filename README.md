# TikTok Saver

This repository provides a tool to save TikTok videos locally.

## Features

-   Downloads a list of videos for each of your collections
-   Saves videos to your system's `Downloads` folder, under `TikTok/<COLLECTION_NAME>`

## Installation

To install the required dependencies, follow these steps:

1. Clone the repository:

    ```sh
    git clone git@github.com:josephdecker1/tiktok-saver.git
    cd tiktok-saver
    ```

2. Install `uv` for Python:

    ```sh
    pip install uv
    ```

3. Run uv sync
    ```sh
    uv sync
    ```

## Usage

To use the TikTok Saver, run the following command from the base of the repository:

```sh
uv run src/browser.py <TIKTOK_USERNAME>
```

Replace `<TIKTOK_USERNAME>` your username, minus any `@` at the beginning.

This script automates a browser to navigate to TikTok and waits for you to log in. Once you've logged in, the script will navigate to your profile and your favorites/collections, grabs all of the collection links, then navigates the browser to each collection to gather a list of video urls and then download a file with the name of your collection to your system's Downloads folder, contained in the `TikTok` folder, minus any special characters.

Once that has completed, you'll run the following command, which will actually download each video into it's respective collection and creates a Sqlite3 db named `tt_metadata_<TIKTOK_USERNAME>.db` in the `Downloads/TikTok/` folder:

```sh
uv run src/tt_dl_db.py <TIKTOK_USERNAME>
```

Any videos that fail to download will be saved to `Downloads/TikTok/failed_downloads.csv`. There are many videos that are unable to be viewed unless a user is logged in - you'll need to download those videos manually, or automate the browser to let you log in and then navigate to those videos. This is a possible future feature.

If desired there is a script under the `src/utils` directory called `download_video.py`. Take a look - you can utilize this script to try and re-download any videos that may be available publicly but the main script wasn't able to download.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## License

This project is licensed under the MIT License.
