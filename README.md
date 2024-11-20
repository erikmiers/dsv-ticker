# dsv-ticker
A script to access the dsv live ticker.

### Install
-   Checkout
-   Create venv: `python -m venv env`
-   Activate venv: `source ./env/bin/activate`
-   Install requirements: `pip install -r requirements.txt`

### Usage
- Run `python dsvticker.py -o` to get an overview over the currently active games
- Copy a game id (in square brackets) looking like this: `[2024_229__L_3]` ==> `2024_229__L_3`
- Run `python dsvticker.py -b 2024_229__L_3` to broadcast game events on port `9001`