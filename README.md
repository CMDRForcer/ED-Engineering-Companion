ED·OPS

ED·OPS is a free, open-source Engineering Companion tool for Elite Dangerous. It helps commanders track blueprints, materials, and wishlists, with live integration to INARA and EDDN.

Features
Engineering & Blueprints
Track engineer unlocks and blueprint progress
Wishlist for planning upgrades and modifications ahead of time
Material Farm view to see what to collect and where
Materials
Live material inventory tracking (raw, manufactured, encoded)
Automatic updates from trading, crafting, synthesis, and collection
CMDR Overview
Combat, Trade, Exploration, CQC, Federation, Empire, Mercenary, and Exobiology ranks
Major-faction reputation (Federation / Empire / Alliance / Independent)
Financial snapshots (credits, assets)
Current ship, minor-faction reputation summary, and squadron info
Logbook
Session history and notes for tracking your journey
INARA Sync

Automatic, rate-limited synchronization of commander data, including:

Materials, cargo, and ship locker inventory
Fleet status (ships, modules, storage)
Commander profile, ranks, and permits
Community goals
EDDN Integration

Contributes data back to the Elite Dangerous Data Network, including:

Market, outfitting, and shipyard listings
Exploration and exobiology data (scan/sample submissions)
Reliability
Honest, state-based smoke testing on every release
Graceful handling of offline periods and external service outages (INARA, EDDN, Spansh)
Persistent, multi-process-safe local state
Installation
Install the required dependencies:
   INSTALL_REQUIREMENTS.bat
Start the application:
   START_APP.bat

See requirements.txt for the full list of Python dependencies.

Tech Stack

Built with Python (PySide6) and QML for the user interface.

Status

ED·OPS is under active development. Contributions, bug reports, and feature suggestions are welcome via GitHub Issues.

License

ED·OPS is licensed under the GNU General Public License v3.0. See LICENSE for the full text.

Support

ED·OPS is free to use. If you find it useful and want to support development, you can buy me a coffee on Ko-fi:

ko-fi.com/cmdrforcer

ED·OPS is a third-party tool and is not affiliated with Frontier Developments. Elite Dangerous is a trademark of Frontier Developments plc.
