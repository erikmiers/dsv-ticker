"""
Constantly connect to the dsvlive webservice and receive infos about ongoing games


"""


import signal
import json
import argparse
import logging
import time

import asyncio
from dataclasses import dataclass
from datetime import datetime
from websockets.asyncio.server import serve as ws_serve

from colorama import init as colorama_init, Fore, Style
import ftfy
from requests import Session
from signalr import Connection

import game_data_model as gdModel

colorama_init()

broadcast_data = {}


class LevelColoredFormatter(logging.Formatter):
    """Simple formatter that only colors the level name"""

    COLORS = {
        'DEBUG': Fore.CYAN,
        'INFO': Fore.WHITE,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
    }

    def format(self, record):
        # Color the levelname according to its level
        record.colored_levelname = f"{self.COLORS.get(record.levelname, '')}"\
        f"{record.levelname}{Style.RESET_ALL}"

        # Use the parent class's format method with our custom format string
        return super().format(record)

# ------------------------------------------------------------------------------
def setup_logging(log_level):
    """
    Set up logging with the specified log level.
    
    Args:
        log_level (str): Desired logging level ('DEBUG', 'INFO', 'WARN')
    """
    # Convert string log level to logging constant
    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f'Invalid log level: {log_level}')

    # Configure logging
    formatter = LevelColoredFormatter(
        fmt = f'{Fore.GREEN}%(asctime)s{Style.RESET_ALL} [%(colored_levelname)s] %(message)s',
        datefmt = '%H:%M:%S'
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    logger = logging.getLogger()
    logger.setLevel(numeric_level)
    logger.handlers.clear()
    logger.addHandler(console_handler)
    # logging.basicConfig(
    #     level=numeric_level,
    #     format=f'{Fore.GREEN}%(asctime)s{Style.RESET_ALL} [%(colored_levelname)s] %(message)s',
    #     datefmt='%H:%M:%S'
    # )

    # Set a higher logging level for urllib3
    logger = logging.getLogger('urllib3')
    logger.setLevel(logging.WARNING)


# ------------------------------------------------------------------------------
def print_game(key, game):
    """
    Print a single line status of a game
    """
    game_time = datetime.fromisoformat(game["StartDate"])
    current_time = datetime.now()
    gametime = game_time.strftime("%d-%m %H:%M")
    hometeam = game["HomeClubname"]
    awayteam = game["GuestClubname"]
    homescore = 0
    awayscore = 0
    for period in game["GoalsPeriods"]:
        homescore += period["HomeGoals"]
        awayscore += period["GuestGoals"]
    gender = game["Gender"]
    game_color = Fore.LIGHTRED_EX if gender == "W" else Fore.MAGENTA if gender == "X" else Fore.BLUE
    league = game["LeagueName"]
    league_short =  "1BL" if league.startswith('1') else \
                    "2-L" if league.startswith('2') else \
                    "U18" if "U18" in league else \
                    "U16" if "U16" in league else \
                    "U14" if "U14" in league else \
                    "1BL" if "Bundesliga" in league else \
                    league
    time_color = Fore.GREEN if current_time < game_time else Fore.GREEN
    time_style = Style.DIM if current_time < game_time else Style.BRIGHT

    print(f"{time_color}{time_style}{gametime}{Style.RESET_ALL} "\
          f"[{Fore.LIGHTBLACK_EX}{key}{Style.RESET_ALL}] "\
          f"{game_color}{league_short}{Style.RESET_ALL} "\
          f"{hometeam} - {awayteam} [{Fore.RED}{homescore}:{awayscore}{Style.RESET_ALL}]")


# ------------------------------------------------------------------------------
def print_overview(games):
    """
    Print an overview of the ongoing games on screen
    """
    print("Currently active games")
    for key in games:
        print_game(key, games[key])


# ------------------------------------------------------------------------------
def create_game_id(game: dict) -> str:
    """Get the game id from the given game data"""
    # 2022_190_A_V_25 <==> Season    : 2022
    #                      LeagueID  : 190
    #                      Group     : A
    #                      LeagueKind: V
    #                      GameID    : 25

    game_id = str(game["Season"]) + "_" + str(game["LeagueID"]) + "_"
    game_id += str(game["Gruppe"])
    game_id += "_" + str(game["LeagueKind"]) + "_" + str(game["GameID"])
    return game_id


# ------------------------------------------------------------------------------
def strip_list_content(key: str, org_data: dict, data_keys: list) -> list:
    """Remove unwanted data from original dict"""
    stripped_list = []
    if not org_data:
        logging.warning("org_data is not defined %s", key)
        return stripped_list
    if not key in org_data:
        logging.warning("Key %s not found org_data", key)
        return stripped_list
    if not org_data[key]:
        logging.debug("Key %s in org_data is NoneType", key)
        return stripped_list
    for entry in org_data[key]:
        # stripped_entry = {k: entry[k] for k in data_keys}
        stripped_entry = {}
        for k in data_keys:
            if k in entry:
                stripped_entry[k] = entry[k]
            else:
                logging.warning("Key %s not found in %s object", k, key)
        fixed_dict = {key: ftfy.fix_text(value) if isinstance(value, str) else value
                      for key, value in stripped_entry.items()}
        stripped_list.append(fixed_dict)
    return stripped_list


# ------------------------------------------------------------------------------
def process_game(game: dict) -> tuple[str, dict]:
    """Process a game by removing all unwanted data"""
    # stripped_data = {key: game[key] for key in gdModel.GAME_DATA_KEYS}
    stripped_data = {}
    for key in gdModel.GAME_DATA_KEYS:
        if key in game:
            stripped_data[key] = game[key]
        else:
            logging.warning("Key %s not found in the 'game' dictionary.", key)

    stripped_data["GamePlan"] = strip_list_content("GamePlan", game,
                                                   gdModel.GAMEPLAN_DATA_KEYS)
    stripped_data["GoalsPeriods"] = strip_list_content("GoalsPeriods", game,
                                                       gdModel.PERIODS_DATA_KEYS)
    stripped_data["HomePlayers"] = strip_list_content("HomePlayers", game,
                                                      gdModel.PLAYERS_DATA_KEYS)
    stripped_data["GuestPlayers"] = strip_list_content("GuestPlayers", game,
                                                       gdModel.PLAYERS_DATA_KEYS)

    fixed_dict = {key: ftfy.fix_text(value) if isinstance(value, str) else value
                    for key, value in stripped_data.items()}

    game_id = create_game_id(fixed_dict)
    return game_id, fixed_dict


# ------------------------------------------------------------------------------
async def connect_to_dsv(terminator, args) -> None:
    """
    Connect to 'https://lizenz.dsv.de/signalr' and listen for requested games
    
    Strip down the objects to only relevant data
    Output the data to stdout

    Write game details to json file
    Will run indefinitely (until SIGINT or SIGTERM is received)
    """

    # --------------------------------------------------------------------------
    def receive_handler(**kwargs) -> None:
        """"Handler for receiving data"""
        # games = {}
        for key, arg in kwargs.items():
            if key == 'M':
                content = "[]" if not arg else "[...]"
                # message = kwargs['M']['M'] if 'M' in kwargs['M'] else content
                logging.debug("M: %s", content)
                continue
            if key != 'R':
                logging.debug("%s: %s", str(key), str(arg))
                continue
            for game in kwargs['R']:
                game_id, game_data = process_game(game)
                logging.info("Game: %s", game_id)
                games[game_id] = game_data
                if args.broadcast and args.broadcast == game_id:
                    global broadcast_data
                    broadcast_data = json.dumps(game, ensure_ascii=False)
        if not games:
            return

        # If game data was received, write it to a json fil
        # json_object = json.dumps(games, ensure_ascii=False)
        # with open("dsvlive.json", "w+",  encoding="utf-8") as outfile:
        #     print("Writing json file " + str(len(json_object)) + " " + str(len(games)))
        #     outfile.write(json_object)


    # --------------------------------------------------------------------------
    def error_handler(*errors) -> None:
        """Handler for errors"""
        logging.error(str(errors))


    # --------------------------------------------------------------------------
    # def handle_add_play(*plays, **_):
    def handle_add_play(*_, **__):
        """Handler for AddPlay function"""
        logging.debug("addPlay called...")
        # game_id, game = process_game(plays[0])

        # game_id = "" #: get game id
        # play = "" # get play from kwargs and strip it down
        # data = {game_id: play}

        # json_object = json.dumps(plays, ensure_ascii=False)
        # with open("plays.json", "w+",  encoding="utf-8") as outfile:
        #     outfile.write(json_object)


    # --------------------------------------------------------------------------
    def handle_update_game(*games, **_):
        """Handler for UpdateGame function"""
        game_id, game = process_game(games[0])
        if args.broadcast and args.broadcast == game_id:
            global broadcast_data
            broadcast_data = json.dumps(game, ensure_ascii=False)
            logging.info("updateGame: [%s] %s - %s", game_id, game["HomeClubname"],
                     game["GuestClubname"])
        # data = {game_id: game}



        # json_object = json.dumps(games, ensure_ascii=False)
        # with open(game_id+"_gu.json", "w+",  encoding="utf-8") as outfile:
        #     outfile.write(json_object)

        # fgames = {}
        # with open("dsvlive.json", "r", encoding="utf-8") as file:
        #     try:
        #         fgames = json.load(file)
        #     except (TypeError, json.JSONDecodeError) as e:
        #         print(e)
        # if not fgames:
        #     fgames = {}
        # fgames[game_id] = game

        # json_object = json.dumps(fgames, ensure_ascii=False)
        # with open("dsvlive.json", "w+",  encoding="utf-8") as outfile:
        #     outfile.write(json_object)


    # --------------------------------------------------------------------------
    def handle_get_all_games(*games, **kwargs):
        """Handler for GetAllGames function - ignored for now"""
        logging.info("getAllGames called: %s %s", len(games), len(kwargs))
        if args.overview:
            terminator.terminate()


    # --------------------------------------------------------------------------
    def handle_r(*attrs, **kwargs):
        """Handler for the R function - will be ignored"""
        logging.info("R called: %s %s", len(attrs), len(kwargs))

    session = Session()
    connection = None
    timeout = 50 if args.overview else -1


    games = {}

    while not terminator.interruption_requested:
        logging.info("initiating new connection")
        connection = Connection("https://lizenz.dsv.de/signalr", session)
        chat = connection.register_hub('wbhub')

        # Process errors, results
        connection.error += error_handler
        connection.exception += error_handler
        connection.received += receive_handler

        # Start connection
        connection.start()
        logging.debug("connection started [%s|%s]",str(connection.is_open),
                     str(connection.started))
        # Register specific handlers
        chat.client.on("addPlay", handle_add_play)
        chat.client.on("updateGame", handle_update_game)
        chat.client.on("getAllGames", handle_get_all_games)
        chat.client.on("R", handle_r)
        # Invoke getAllGames method
        chat.server.invoke('getAllGames')
        logging.info("connection initiated [%s|%s]", str(connection.is_open),
              str(connection.started))

        count = 0
        while connection.is_open:
            if terminator.interruption_requested:
                logging.debug("closing: terminator")
                break
            if count > 72000: # 2 hours
                logging.debug("closing: counter auto renew")
                break
            if 0 < timeout < count:
                logging.debug("closing: timeout")
                terminator.terminate()
                break
            # Wait till connection is closed (due to errors or timeouts)
            #      or an interruption is requested

            # Refresh connection roughly every 30 minutes
            count += 1
            await asyncio.sleep(.1)
        logging.info("connection terminated %s", str(connection.is_open))
        try:
            print_overview(games)
        except Exception as e:
            print(f"An error occurred: {e}")
            exit(0)

        if connection and connection.is_open:
            connection.close()

    session.close()


# ------------------------------------------------------------------------------
async def handle_connection(websocket):
    """
    Handle incomming websocket connections
    """
    while True:
        await websocket.send(broadcast_data)
        await asyncio.sleep(1)


# ------------------------------------------------------------------------------
async def main():
    """
    The main method
    """
    arg_parser = argparse.ArgumentParser(
        description='Commandline access to the DSV-Live Ticker',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
        --------------------------------
        Author: erik.miers+dsv@gmail.com
        --------------------------------
        """
    )
    arg_parser.add_argument(
        "--debug",
        action="store_true",
        help="Set logging output to DEBUG level",
    )
    arg_parser.add_argument(
        "--log-level",
        choices=["INFO", "DEBUG", "WARN"],
        default="INFO",
        help="Set logging level\n\n",
    )
    arg_parser.add_argument(
        "-o",
        "--overview",
        action="store_true",
        help="Get an overview over currently running and upcoming games",
    )
    arg_parser.add_argument(
        "-t",
        "--ticker",
        action="store_true",
        help="Get score updates of the currently running games",
    )
    arg_parser.add_argument(
        "-d",
        "--details",
        metavar="ID",
        type=str,
        help="Get score updates and details of a spcific game",
    )
    arg_parser.add_argument(
        "-b",
        "--broadcast",
        metavar="ID",
        type=str,
        help="Broadcast all game updates over a local websocket",
    )
    arguments = arg_parser.parse_args()
    if arguments.debug:
        arguments.log_level = "DEBUG"
    setup_logging(arguments.log_level)


    server = None
    if arguments.broadcast:
        server = await ws_serve(handle_connection, "0.0.0.0", 9001)

    # server_task = ( asyncio.create_task(start_websocket_server())
    #                 if arguments.broadcast else None )

    @dataclass
    class Terminator:
        """The Terminator class"""
        interruption_requested = False

        def __init__(self):
            signal.signal(signal.SIGINT, self.terminate)
            signal.signal(signal.SIGTERM, self.terminate)

        def terminate(self, *_):
            """The terminator function"""
            self.interruption_requested = True
    dsv_task = asyncio.create_task(connect_to_dsv(Terminator(), arguments))
    await dsv_task
    if server:
        server.close()
        await server.wait_closed()

    # if start_server:
    #     await start_server
    # await dsv_task

    # try:
    #     if server_task:
    #         results = asyncio.gather(dsv_task, server_task, return_exceptions=True)
    #         for result in results:
    #             if isinstance(result, Exception):
    #                 logging.error(f"Task raised an exception: {result}")
    #     else:
    #         await dsv_task
    # except asyncio.CancelledError:
    #     logging.info("asyncio.CancelledError")
    #     if server_task:
    #         server_task.cancel()
    #     dsv_task.cancel()
    #     await asyncio.gather( *(task for task in [dsv_task, server_task] if task), 
    #                          return_exceptions=True)


# ------------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt")
