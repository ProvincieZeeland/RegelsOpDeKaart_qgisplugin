import os
import requests
import json
from collections import defaultdict
from shapely.geometry import shape, Polygon, MultiPolygon, LineString, GeometryCollection
from shapely.ops import unary_union
from shapely.errors import TopologicalError
from PyQt5.QtCore import QSettings, Qt, QVariant, QSize
from PyQt5.QtGui import QIcon, QCursor, QColor, QPixmap, QPainter
from PyQt5.QtWidgets import QAction, QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, \
    QListWidgetItem, QSplitter, QAbstractItemView, QLabel, QDialogButtonBox, QLineEdit, QTableWidget, QTableWidgetItem,\
    QHeaderView, QMenu, QApplication, QLineEdit, QComboBox

from qgis.core import QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY, QgsField, QgsWkbTypes, \
    QgsLayerTreeGroup
from qgis.gui import QgsMapTool, QgsRubberBand

import logging
# Setup basic logging
logging.basicConfig(level=logging.INFO)


# Constants
PLAN_TYPES = ["uitwerkingsplan", "wijzigingsplan", "inpassingsplan", "beheersverordening", "bestemmingsplan",
              "exploitatieplan", "tijdelijke ontheffing buitenplans", "omgevingsvergunning", "projectbesluit",
              "reactieve aanwijzing", "gerechtelijke uitspraak", "voorbereidingsbesluit", "provinciale verordening",
              "aanwijzingsbesluit", "amvb", "regeling", "structuurvisie", "rijksbestemmingsplan",
              "buiten toepassing verklaring beheersverordening", "gemeentelijke visie; overig",
              "gemeentelijk besluit; overig"]

PLAN_STATUSES = ["concept", "voorontwerp", "ontwerp", "vastgesteld", "geconsolideerd", "onherroepelijk",
                 "geconsolideerde versie", "beroep afdeling bestuursrechtspraak", "kabinetsvoornemen", "goedgekeurd",
                 "goedgekeurd; geheel goedgekeurd", "goedgekeurd; goedgekeurd met uitzondering van onthoudingen",
                 "goedkeuring onthouden", "kabinetsstandpunt", "resultaten van inspraak bestuurlijk overleg en advies",
                 "uitspraak afdeling bestuursrechtspraak", "uitspraak afdeling bestuursrechtspraak: alsnog goedgekeurd",
                 "uitspraak afdeling bestuursrechtspraak: alsnog goedkeuring onthouden", "vastgesteld beleid",
                 "vigerend",
                 "voorlopige voorziening"]

QML_PATH = os.path.join(os.path.dirname(__file__), 'qml')

class CustomPointTool(QgsMapTool):
    def __init__(self, canvas, api_key, plugin):
        super().__init__(canvas)
        self.canvas = canvas
        self.api_key = api_key
        self.plugin = plugin
        self.point = None

    def canvasPressEvent(self, event):
        self.point = self.toMapCoordinates(event.pos())
        self.plugin.show_plan_type_dialog([self.point])  # Show the filter dialog

    def activate(self):
        # Set a custom cursor with a small circle
        cursor_pixmap = QPixmap(20, 20)  # Example size, you can adjust
        cursor_pixmap.fill(Qt.transparent)

        painter = QPainter(cursor_pixmap)
        painter.setBrush(Qt.red)  # Example color, you can change
        painter.drawEllipse(5, 5, 10, 10)  # Draw a circle
        painter.end()

        custom_cursor = QCursor(cursor_pixmap)
        self.canvas.setCursor(custom_cursor)
        self.plugin.custom_tool = self  # Ensure it's the active tool

    def deactivate(self):
        self.canvas.unsetCursor()

    def isZoomTool(self):
        return False

    def isTransient(self):
        return False

    def isEditTool(self):
        return True


class CustomPolygonTool(QgsMapTool):
    def __init__(self, canvas, api_key, plugin):
        super().__init__(canvas)
        self.canvas = canvas
        self.rubberBand = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rubberBand.setColor(QColor(255, 0, 0, 128))  # Red color with opacity
        self.rubberBand.setWidth(1)
        self.points = []
        self.api_key = api_key
        self.plugin = plugin

    def canvasPressEvent(self, event):
        point = self.toMapCoordinates(event.pos())
        self.points.append(point)
        self.rubberBand.addPoint(point, True)
        self.rubberBand.show()

    def canvasReleaseEvent(self, event):
        pass

    def canvasMoveEvent(self, event):
        if not self.points:
            return
        point = self.toMapCoordinates(event.pos())
        self.rubberBand.movePoint(point)

    def canvasDoubleClickEvent(self, event):
        self.complete_polygon()

    def canvasRightClickEvent(self, event):
        self.complete_polygon()

    def complete_polygon(self):
        if len(self.points) < 3:
            QMessageBox.warning(None, "Warning", "A polygon requires at least 3 points.")
            return
        if self.points[0] != self.points[-1]:
            self.points.append(self.points[0])
        self.rubberBand.closePoints()
        self.plugin.show_plan_type_dialog(self.points)
        self.rubberBand.reset(QgsWkbTypes.PolygonGeometry)
        self.points = []

    def activate(self):
        self.canvas.setCursor(Qt.CrossCursor)
        self.plugin.custom_tool = self  # Ensure it's the active tool

    def deactivate(self):
        self.rubberBand.hide()
        self.canvas.unsetCursor()

    def isZoomTool(self):
        return False

    def isTransient(self):
        return False

    def isEditTool(self):
        return True


class RuimtelijkePlannen:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = os.path.dirname(__file__)
        self.toolbar = self.iface.addToolBar('Ruimtelijke Plannen')
        self.toolbar.setObjectName('Ruimtelijke Plannen')

        # Initialize locale
        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(self.plugin_dir, 'i18n', 'RuimtelijkePlannen_{}.qm'.format(locale))
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.api_key = QSettings().value('RuimtelijkePlannen/api_key', '')
        self.method_choice = QSettings().value('RuimtelijkePlannen/method_choice', 'Polygon Click')

    def initGui(self):
        # Add actions
        self.add_actions()

        # Load saved API key and method choice
        self.api_key = QSettings().value('RuimtelijkePlannen/api_key', '')
        self.method_choice = QSettings().value('RuimtelijkePlannen/method_choice', 'Polygon Click')

        # Activate the correct tool based on the saved method_choice
        self.activate_custom_tool()

        # Set the icon path
        icon_path = os.path.join(self.plugin_dir, 'icons', 'RuimtelijkePlannen.svg')

        # Create an action with the icon
        self.action = QAction(
            QIcon(icon_path),
            "Ruimtelijke Plannen",
            self.iface.mainWindow()
        )

        # Add the action to the plugin menu and toolbar
        self.iface.addPluginToMenu("&Ruimtelijke Plannen", self.action)
        self.iface.addToolBarIcon(self.action)

    def add_actions(self):
        # Main button to draw polygon
        self.main_action = QAction(QIcon(os.path.join(self.plugin_dir, 'icons', 'RuimtelijkePlannen.svg')), 'Draw Polygon',
                                   self.iface.mainWindow())
        self.main_action.triggered.connect(self.activate_custom_tool)
        self.toolbar.addAction(self.main_action)

        # Settings button
        self.settings_action = QAction(QIcon(os.path.join(self.plugin_dir, 'icons', 'settings_icon.svg')),
                                       'API Settings', self.iface.mainWindow())
        self.settings_action.triggered.connect(self.show_settings_dialog)
        self.toolbar.addAction(self.settings_action)

        # Search bar for IMRO code
        self.search_bar = QLineEdit(self.toolbar)
        self.search_bar.setPlaceholderText("Enter IMRO code")
        self.search_bar.returnPressed.connect(self.on_search)  # Connect to the on_search method
        self.toolbar.addWidget(self.search_bar)

    def on_search(self):
        imro_code = self.search_bar.text()  # Retrieve the text from the QLineEdit
        if imro_code:
            self.fetch_and_import_plan_by_imro_code(imro_code)  # Pass the IMRO code to the method
        else:
            QMessageBox.warning(None, "Error", "Please enter a valid IMRO code.")

    def activate_custom_tool(self):
        # Deactivate any active custom tool
        if hasattr(self, 'custom_tool') and self.custom_tool:
            self.custom_tool.deactivate()

        # Activate the correct tool based on the method_choice
        if self.method_choice == "Polygon Click":
            self.custom_tool = CustomPolygonTool(self.canvas, self.api_key, self)
        elif self.method_choice == "Point Click":
            self.custom_tool = CustomPointTool(self.canvas, self.api_key, self)

        # Set the new tool as active
        self.canvas.setMapTool(self.custom_tool)

    def show_settings_dialog(self):
        dialog = SettingsDialog()
        dialog.set_api_key(self.api_key)
        dialog.method_choice.setCurrentText(self.method_choice)
        if dialog.exec_():
            self.api_key = dialog.get_api_key()
            self.method_choice = dialog.get_method_choice()
            QSettings().setValue('RuimtelijkePlannen/api_key', self.api_key)
            QSettings().setValue('RuimtelijkePlannen/method_choice', self.method_choice)

            # Activate the tool based on the new method choice
            self.activate_custom_tool()

    def show_plan_type_dialog(self, points):
        dialog = PlanTypeDialog()
        if dialog.exec_():
            selected_plan_types = dialog.get_selected_plan_types()
            selected_statuses = dialog.get_selected_statuses()
            if not selected_plan_types and not selected_statuses:
                reply = QMessageBox.question(None, 'Confirmation',
                                             'No plan types or statuses selected. Are you sure you want to continue?',
                                             QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply == QMessageBox.No:
                    return
            self.request_coordinates(points, selected_plan_types, selected_statuses)


    def request_coordinates(self, points, selected_plan_types=None, selected_statuses=None, date_from=None,
                            date_to=None):
        if not self.api_key:
            QMessageBox.warning(None, "Error", "API key is missing. Please enter the API key in the settings.")
            return

        if len(points) == 1:
            coordinates = [points[0].x(), points[0].y()]
            geo_json = {
                "type": "Point",
                "coordinates": coordinates
            }
        else:
            coordinates = [(point.x(), point.y()) for point in points]
            geo_json = {
                "type": "Polygon",
                "coordinates": [coordinates]
            }

        url = 'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/_zoek'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Content-Crs': 'epsg:28992'
        }

        payload = {
            "planType": selected_plan_types if selected_plan_types else None,
            "status": selected_statuses if selected_statuses else None,
            "_geo": {
                "intersects": geo_json
            },
            "planstatusdatumNa": date_from if date_from else None,  # Filter by start date
            "planstatusdatumVoor": date_to if date_to else None  # Filter by end date
        }

        params = {
            'pageSize': 100,
            'expand': 'geometrie'
        }

        response = requests.post(url, json=payload, headers=headers, params=params)
        response.raise_for_status()

        data = response.json()
        self.show_data_in_table(data)

    def show_data_in_table(self, data):
        self.table_widget = QTableWidget()

        # Update column count to include new columns
        self.table_widget.setRowCount(len(data['_embedded']['plannen']))
        self.table_widget.setColumnCount(7)  # Adjust based on the number of fields

        # Update column headers to include the new columns
        self.table_widget.setHorizontalHeaderLabels(
            ['PlanID', 'Naam', 'Type', 'Status', 'Verwijzing', 'Datum', 'Bevoegd gezag']
        )
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

        # Set selection behavior to select rows
        self.table_widget.setSelectionBehavior(QTableWidget.SelectRows)

        for row, plan in enumerate(data['_embedded']['plannen']):
            # Extract relevant data from the plan
            plan_id = QTableWidgetItem(plan.get('id', None))
            name = QTableWidgetItem(plan.get('naam', None))
            plan_type = QTableWidgetItem(plan.get('type', None))
            status = QTableWidgetItem(plan.get('planstatusInfo', {}).get('planstatus', None))
            verwijzing = QTableWidgetItem(plan.get('verwijzingNaarVaststellingsbesluit', None))
            datum = QTableWidgetItem(plan.get('planstatusInfo',{}).get('datum', None))
            bevoegd_gezag = QTableWidgetItem(plan.get('publicerendBevoegdGezag',{}).get('naam', None))

            # Insert items into the table
            self.table_widget.setItem(row, 0, plan_id)
            self.table_widget.setItem(row, 1, name)
            self.table_widget.setItem(row, 2, plan_type)
            self.table_widget.setItem(row, 3, status)
            self.table_widget.setItem(row, 4, verwijzing)
            self.table_widget.setItem(row, 5, datum)  # Add Datum data
            self.table_widget.setItem(row, 6, bevoegd_gezag)  # Add Bevoegd gezag data

        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.customContextMenuRequested.connect(self.copy_context_menu)
        self.table_widget.setSortingEnabled(True)

        dialog = QDialog()
        dialog.setWindowTitle('API Response Data')
        layout = QVBoxLayout()
        layout.addWidget(self.table_widget)

        button_layout = QHBoxLayout()
        import_button = QPushButton('Import selected')
        import_button.clicked.connect(lambda: self.import_selected_layers(dialog))
        button_layout.addWidget(import_button)

        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        dialog.resize(
            (self.table_widget.sizeHintForColumn(0) + self.table_widget.sizeHintForColumn(1) +
             self.table_widget.sizeHintForColumn(2) + self.table_widget.sizeHintForColumn(3) +
             self.table_widget.sizeHintForColumn(4) + self.table_widget.sizeHintForColumn(5) +
             self.table_widget.sizeHintForColumn(6) + 50) // 2,
            (self.table_widget.sizeHintForRow(0) * len(data['_embedded']['plannen']) + 50) // 2
        )
        dialog.exec_()

    def copy_context_menu(self, pos):
        menu = QMenu()
        copy_action = QAction('Copy', self.table_widget)
        copy_action.triggered.connect(self.copy_selected)
        menu.addAction(copy_action)
        menu.exec_(QCursor.pos())

    def copy_selected(self):
        selected_items = self.table_widget.selectedItems()
        if selected_items:
            clipboard = QApplication.clipboard()
            clipboard.setText(selected_items[0].text())

    def import_selected_layers(self, dialog):
        selected_rows = set(item.row() for item in self.table_widget.selectedItems())
        if not selected_rows:
            QMessageBox.warning(None, "Warning", "No records selected.")
            return

        for row in selected_rows:
            plan_id_item = self.table_widget.item(row, 0)  # Assuming plan_id is in the first column
            name_item = self.table_widget.item(row, 1)  # Assuming name is in the second column

            if plan_id_item and name_item:
                plan_id = plan_id_item.text()
                name = name_item.text()

                try:
                    self.fetch_and_import_plan(plan_id, name, row)
                except requests.exceptions.HTTPError as http_err:
                    if http_err.response.status_code == 500:
                        logging.error(f"500 Error: Internal Server Error while importing Plan ID: {plan_id}")
                        QMessageBox.warning(None, "Error",
                                            f"Failed to import Plan ID {plan_id} due to an Internal Server Error.")
                        break  # Stop the import process if a 500 error occurs
                    elif http_err.response.status_code == 404:
                        logging.error(f"404 Error: Plan not found for Plan ID: {plan_id}")
                        QMessageBox.warning(None, "Error",
                                            f"Plan not found or no geometry was found for Plan ID {plan_id}.")
                        break  # Stop the import process if a 404 error occurs
                    else:
                        logging.error(f"HTTP error occurred for Plan ID: {plan_id} - {http_err}")
                        QMessageBox.warning(None, "Error",
                                            f"HTTP error occurred: {http_err} - Response content: {http_err.response.content.decode('utf-8', errors='ignore')}")
                        break  # Stop the import process if an unexpected HTTP error occurs
                except AttributeError as attr_err:
                    logging.error(f"AttributeError occurred for Plan ID: {plan_id} - {attr_err}")
                    QMessageBox.warning(None, "Error",
                                        f"An error occurred while processing Plan ID {plan_id}. It appears that some data is missing or corrupt.")
                    break  # Stop the import process if an AttributeError occurs
                except RuntimeError as run_err:
                    if 'QgsLayerTreeGroup has been deleted' in str(run_err):
                        logging.error(f"RuntimeError occurred: {run_err}")
                        QMessageBox.warning(None, "Error",
                                            f"The layer group for Plan ID {plan_id} has been deleted. The import process will stop.")
                        break  # Stop the import process if the layer group was deleted
                    else:
                        logging.error(f"RuntimeError occurred for Plan ID: {plan_id} - {run_err}")
                        QMessageBox.warning(None, "Error",
                                            f"RuntimeError occurred: {run_err}")
                        break
                except Exception as err:
                    logging.error(f"An unexpected error occurred for Plan ID: {plan_id} - {err}")
                    QMessageBox.warning(None, "Error", f"An unexpected error occurred: {err}")
                    break  # Stop the import process if an unexpected error occurs

        dialog.accept()


    def fetch_and_import_plan(self, plan_id, name, row):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            plan_data = response.json()
            # Check if plan_data exists and contains geometrie, otherwise try to fetch it separately
            if not plan_data or 'geometrie' not in plan_data or not plan_data['geometrie']:
                plan_data = self.query_plan_geometry(plan_id)

            # If plan_data is still None or invalid, show an error
            if not plan_data or 'geometrie' not in plan_data or not plan_data['geometrie']:
                QMessageBox.warning(None, "Error", f"Failed to retrieve or process plan data for Plan ID {plan_id}.")
                return

            group_name = f"{plan_id} - {name}"
            group = QgsProject.instance().layerTreeRoot().insertGroup(0, group_name)

            plan_layer_group = group.addGroup("Plan")
            self.add_plan_to_layers(plan_data, name, plan_layer_group, row)

            # Query and add layers with error handling
            query_layers = [
                ("Bouwvlakken", self.query_and_add_bouwvlakken),
                ("Functieaanduidingen", self.query_and_add_functieaanduidingen),
                ("Bouwaanduidingen", self.query_and_add_bouwaanduidingen),
                ("Lettertekenaanduidingen", self.query_and_add_lettertekenaanduidingen),
                ("Maatvoeringen", self.query_and_add_maatvoeringen),
                ("Figuren", self.query_and_add_figuren),
                ("Gebiedsaanduidingen", self.query_and_add_gebiedsaanduidingen),
                ("Structuurvisiegebieden", self.query_and_add_structuurvisiegebieden),
                ("Structuurvisiecomplexen", self.query_and_add_structuurvisiecomplexen),
                ("Besluitvlakken", self.query_and_add_besluitvlakken),
                ("Besluitsubvlakken", self.query_and_add_besluitsubvlakken),
                ("Bekendmakingen", self.query_and_add_bekendmakingen),
                ("Bestemmingsvlakken", self.query_and_add_bestemmingsvlakken)
            ]

            for layer_name, query_method in query_layers:
                self.safe_query_and_add_layer(plan_id, group, layer_name, query_method)

        except requests.exceptions.HTTPError as http_err:
            if response.status_code == 500:
                QMessageBox.warning(None, "Error",
                                    f"500 Error: Too much data requested for Plan ID {plan_id}. Server responded with: {response.content.decode('utf-8', errors='ignore')}")
            elif response.status_code == 404:
                QMessageBox.warning(None, "Error", f"Plan ID {plan_id} not found: {http_err}")
            else:
                QMessageBox.warning(None, "Error",
                                    f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while fetching and importing plan: {err}")

    def safe_query_and_add_layer(self, plan_id, group, layer_name, query_method):
        try:
            sub_group = group.addGroup(layer_name)
            query_method(plan_id, sub_group)
        except requests.exceptions.HTTPError as http_err:
            if http_err.response.status_code == 404:
                QMessageBox.warning(None, "Error", f"{layer_name} not found for Plan ID {plan_id}: {http_err}")
            elif http_err.response.status_code == 500:
                QMessageBox.warning(None, "Error",
                                    f"Internal Server Error while querying {layer_name} for Plan ID {plan_id}: {http_err}")
            else:
                QMessageBox.warning(None, f"Error importing {layer_name}: HTTP error occurred: {http_err}")
        except AttributeError as attr_err:
            QMessageBox.warning(None, "Error", f"Error importing {layer_name}: AttributeError - {attr_err}")
        except RuntimeError as run_err:
            if 'QgsLayerTreeGroup has been deleted' in str(run_err):
                QMessageBox.warning(None, "Error",
                                    f"Error importing {layer_name}: The layer group for Plan ID {plan_id} has been deleted.")
            else:
                QMessageBox.warning(None, "Error", f"Error importing {layer_name}: RuntimeError - {run_err}")
        except TypeError as type_err:
            QMessageBox.warning(None, "Error", f"Error importing {layer_name}: TypeError - {type_err}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Error importing {layer_name}: {err}")

    def query_plan_geometry(self, plan_id):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}?expand=geometrie'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
            return {}
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading plan geometry: {err}")
            return {}

    def add_plan_to_layers(self, plan_data, name, group, row=None):
        geometry = plan_data.get('geometrie')
        if geometry is None:
            logging.error(f"Plan ID {plan_data.get('id')} has no geometry.")
            QMessageBox.warning(None, "Error", f"Plan ID {plan_data.get('id')} has no geometry.")
            return

        geometry = shape(geometry)

        if isinstance(geometry, Polygon):
            polygons = [geometry]
        elif isinstance(geometry, MultiPolygon):
            polygons = geometry.geoms
        else:
            logging.error(f"Unsupported geometry type for Plan ID {plan_data.get('id')}.")
            QMessageBox.warning(None, "Error", f"Unsupported geometry type for Plan ID {plan_data.get('id')}.")
            return

        # Proceed to create the layer only if geometry and attributes are valid
        if polygons:
            layer = QgsVectorLayer('Polygon?crs=EPSG:28992', name, 'memory')
            provider = layer.dataProvider()
            if not provider:
                logging.error(f"Failed to get data provider for the layer {name}.")
                QMessageBox.warning(None, "Error", f"Failed to create data provider for layer {name}.")
                return

            provider.addAttributes([
                QgsField('plan_id', QVariant.String),
                QgsField('name', QVariant.String),
                QgsField('type', QVariant.String),
                QgsField('status', QVariant.String),
                QgsField('verwijzing', QVariant.String),
                QgsField('beleidsmatigVerantwoordelijkeOverheid.code', QVariant.String),
                QgsField('beleidsmatigVerantwoordelijkeOverheid.naam', QVariant.String),
                QgsField('beleidsmatigVerantwoordelijkeOverheid.type', QVariant.String),
                QgsField('publicerendBevoegdGezag.code', QVariant.String),
                QgsField('publicerendBevoegdGezag.naam', QVariant.String),
                QgsField('publicerendBevoegdGezag.type', QVariant.String),
                QgsField('dossier.id', QVariant.String),
                QgsField('planType', QVariant.String),
                QgsField('planFilter', QVariant.String),
                QgsField('overgangsrecht', QVariant.String),
                QgsField('regelStatus', QVariant.String),
                QgsField('regelBinding', QVariant.String),
                QgsField('planstatusdatumNa', QVariant.String),
                QgsField('planstatusdatumVoor', QVariant.String),
                QgsField('beschikbaarOp', QVariant.String),
                QgsField('isTamPlan', QVariant.Bool)
            ])
            layer.updateFields()

            attributes = [
                plan_data.get('id', None),
                name,
                plan_data.get('type', None),
                plan_data.get('planstatusInfo', {}).get('planstatus', None),
                plan_data.get('verwijzingNaarVaststellingsbesluit', None),
                plan_data.get('beleidsmatigVerantwoordelijkeOverheid', {}).get('code', None),
                plan_data.get('beleidsmatigVerantwoordelijkeOverheid', {}).get('naam', None),
                plan_data.get('beleidsmatigVerantwoordelijkeOverheid', {}).get('type', None),
                plan_data.get('publicerendBevoegdGezag', {}).get('code', None),
                plan_data.get('publicerendBevoegdGezag', {}).get('naam', None),
                plan_data.get('publicerendBevoegdGezag', {}).get('type', None),
                plan_data.get('dossier', {}).get('id', None),
                plan_data.get('type', None),
                plan_data.get('naam', None),
                plan_data.get('overgangsrecht', None),
                plan_data.get('regelStatus', None),
                plan_data.get('regelBinding', None),
                plan_data.get('planstatusdatumNa', None),
                plan_data.get('planstatusdatumVoor', None),
                plan_data.get('beschikbaarOp', None),
                plan_data.get('isTamPlan', None)
            ]

            for polygon in polygons:
                feature = QgsFeature()
                feature.setGeometry(
                    QgsGeometry.fromPolygonXY([[QgsPointXY(*coord) for coord in polygon.exterior.coords]]))
                feature.setAttributes(attributes)
                provider.addFeature(feature)

            QgsProject.instance().addMapLayer(layer, False)
            group.addLayer(layer)
            layer.loadNamedStyle(os.path.join(QML_PATH, 'plangebied.qml'))
            layer.triggerRepaint()

        else:
            logging.warning(f"No valid polygons found for Plan ID {plan_data.get('id')}.")
            QMessageBox.warning(None, "Error", f"No valid polygons found for Plan ID {plan_data.get('id')}.")

    def query_and_add_bestemmingsvlakken(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/bestemmingsvlakken'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('bestemmingsvlakken', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_bestemmingsvlakken_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred while loading bestemmingsvlakken: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading bestemmingsvlakken: {err}")

    def query_and_add_bouwvlakken(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/bouwvlakken'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('bouwvlakken', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_bouwvlakken_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading bouwvlakken: {err}")

    def query_and_add_functieaanduidingen(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/functieaanduidingen'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('functieaanduidingen', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_functieaanduidingen_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading functieaanduidingen: {err}")

    def query_and_add_bouwaanduidingen(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/bouwaanduidingen'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('bouwaanduidingen', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_bouwaanduidingen_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading bouwaanduidingen: {err}")

    def query_and_add_lettertekenaanduidingen(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/lettertekenaanduidingen'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('lettertekenaanduidingen', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_lettertekenaanduidingen_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading lettertekenaanduidingen: {err}")

    def query_and_add_maatvoeringen(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/maatvoeringen'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('maatvoeringen', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_maatvoeringen_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading maatvoeringen: {err}")

    def query_and_add_figuren(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/figuren'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('figuren', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_figuren_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading figuren: {err}")

    def query_and_add_gebiedsaanduidingen(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/gebiedsaanduidingen'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('gebiedsaanduidingen', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_gebiedsaanduidingen_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading gebiedsaanduidingen: {err}")


    def query_and_add_structuurvisiegebieden(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/structuurvisiegebieden'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 10  # Start with a smaller page size to avoid large data issues
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                if response.status_code == 502:  # Handle 502 Bad Gateway error specifically
                    raise requests.exceptions.RequestException("502 Bad Gateway")
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('structuurvisiegebieden', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < params['pageSize']:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_structuurvisiegebieden_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            if response.status_code == 500:
                QMessageBox.warning(None, "Error",
                                    f"500 Error: Too much data requested for Plan ID {plan_id}. Server responded with: {response.content.decode('utf-8', errors='ignore')}")
            else:
                QMessageBox.warning(None, "Error",
                                    f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except requests.exceptions.RequestException as req_err:
            QMessageBox.warning(None, "Error",
                                f"Request error occurred: {req_err}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading structuurvisiegebieden: {err}")

    def query_and_add_structuurvisiecomplexen(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/structuurvisiecomplexen'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('structuurvisiecomplexen', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_structuurvisiecomplexen_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading structuurvisiecomplexen: {err}")

    def query_and_add_besluitvlakken(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/besluitvlakken'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('besluitvlakken', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_besluitvlakken_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading besluitvlakken: {err}")

    def query_and_add_besluitsubvlakken(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/besluitsubvlakken'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('besluitsubvlakken', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_besluitsubvlakken_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading besluitsubvlakken: {err}")

    def query_and_add_bekendmakingen(self, plan_id, group):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{plan_id}/bekendmakingen'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }
        params = {
            'expand': 'geometrie',
            'page': 1,
            'pageSize': 100
        }

        try:
            all_vlakken = []
            while True:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                vlakken = data.get('_embedded', {}).get('bekendmakingen', [])
                all_vlakken.extend(vlakken)
                if len(vlakken) < 100:
                    break
                params['page'] += 1

            if all_vlakken:
                self.add_bekendmakingen_to_layers(all_vlakken, group)
            else:
                parent = group.parent()
                parent.removeChildNode(group)

        except requests.exceptions.HTTPError as http_err:
            QMessageBox.warning(None, "Error",
                                f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except Exception as err:
            QMessageBox.warning(None, "Error", f"Other error occurred while loading bekendmakingen: {err}")

    def add_bestemmingsvlakken_to_layers(self, vlakken, group):
        enkelbestemming_layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Enkelbestemming', 'memory')
        dubbelbestemming_layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Dubbelbestemming', 'memory')
        enkel_provider = enkelbestemming_layer.dataProvider()
        dubbel_provider = dubbelbestemming_layer.dataProvider()

        enkel_provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('type', QVariant.String),
            QgsField('labelInfo', QVariant.String),
            QgsField('bestemmingshoofdgroep', QVariant.String),
            QgsField('styling_order', QVariant.Int)
        ])
        dubbel_provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('type', QVariant.String),
            QgsField('labelInfo', QVariant.String),
            QgsField('styling_order', QVariant.Int)
        ])
        enkelbestemming_layer.updateFields()
        dubbelbestemming_layer.updateFields()

        enkelbestemming_geometries = defaultdict(list)

        for vlak in vlakken:
            geometry = shape(vlak.get('geometrie'))
            properties = {
                'id': vlak.get('id', None),
                'naam': vlak.get('naam', None),
                'type': vlak.get('type', None),
                'labelInfo': vlak.get('labelInfo', None),
                'bestemmingshoofdgroep': vlak.get('bestemmingshoofdgroep', '').lower()
            }

            if properties['type'] == 'enkelbestemming':
                enkelbestemming_geometries[properties['bestemmingshoofdgroep']].append((geometry, properties))
            else:
                properties['styling_order'] = self.get_styling_order(properties['bestemmingshoofdgroep'])
                feature = QgsFeature()
                feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))
                feature.setAttributes([
                    properties['id'], properties['naam'], properties['type'], properties['labelInfo'],
                    properties['styling_order']
                ])
                dubbel_provider.addFeature(feature)

        for hoofdgroep, geometries in enkelbestemming_geometries.items():
            for geometry, properties in geometries:
                feature = QgsFeature()
                feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))
                feature.setAttributes([
                    properties['id'], properties['naam'], properties['type'], properties['labelInfo'],
                    properties['bestemmingshoofdgroep'], self.get_styling_order(hoofdgroep)
                ])
                enkel_provider.addFeature(feature)

        QgsProject.instance().addMapLayer(dubbelbestemming_layer, False)
        group.insertLayer(0, dubbelbestemming_layer)
        QgsProject.instance().addMapLayer(enkelbestemming_layer, False)
        group.insertLayer(1, enkelbestemming_layer)

        dubbelbestemming_layer.loadNamedStyle(os.path.join(QML_PATH, 'dubbelbestemming_digitaal.qml'))
        dubbelbestemming_layer.triggerRepaint()
        enkelbestemming_layer.loadNamedStyle(os.path.join(QML_PATH, 'enkelbestemming_imro_qgis.qml'))
        enkelbestemming_layer.triggerRepaint()


    def add_bouwvlakken_to_layers(self, vlakken, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Bouwvlakken', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String)
        ])
        layer.updateFields()

        for vlak in vlakken:
            geometry = shape(vlak.get('geometrie'))
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))

            properties = {
                'id': vlak.get('id', None),
                'naam': vlak.get('naam', None)
            }

            feature.setAttributes([properties['id'], properties['naam']])
            provider.addFeature(feature)

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'bouwvlakken.qml'))

    def add_functieaanduidingen_to_layers(self, vlakken, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Functieaanduidingen', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('labelInfo', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String)
        ])
        layer.updateFields()

        for vlak in vlakken:
            geometry = shape(vlak.get('geometrie'))
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))

            properties = {
                'id': vlak.get('id', None),
                'naam': vlak.get('naam', None),
                'labelInfo': vlak.get('labelInfo', None),
                'verwijzingNaarTekst': vlak.get('verwijzingNaarTekst', None)
            }

            feature.setAttributes(
                [properties['id'], properties['naam'], properties['labelInfo'], properties['verwijzingNaarTekst']])
            provider.addFeature(feature)

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'functieaanduidingen.qml'))

    def add_bouwaanduidingen_to_layers(self, vlakken, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Bouwaanduidingen', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('labelInfo', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String)
        ])
        layer.updateFields()

        for vlak in vlakken:
            geometry = shape(vlak.get('geometrie'))
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))

            properties = {
                'id': vlak.get('id', None),
                'naam': vlak.get('naam', None),
                'labelInfo': vlak.get('labelInfo', None),
                'verwijzingNaarTekst': vlak.get('verwijzingNaarTekst', None)
            }

            feature.setAttributes(
                [properties['id'], properties['naam'], properties['labelInfo'], properties['verwijzingNaarTekst']])
            provider.addFeature(feature)

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'bouwaanduidingen.qml'))

    def add_lettertekenaanduidingen_to_layers(self, vlakken, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Lettertekenaanduidingen', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('labelInfo', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String)
        ])
        layer.updateFields()

        for vlak in vlakken:
            geometry = shape(vlak.get('geometrie'))
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))

            properties = {
                'id': vlak.get('id', None),
                'naam': vlak.get('naam', None),
                'labelInfo': vlak.get('labelInfo', None),
                'verwijzingNaarTekst': vlak.get('verwijzingNaarTekst', None)
            }

            feature.setAttributes(
                [properties['id'], properties['naam'], properties['labelInfo'], properties['verwijzingNaarTekst']])
            provider.addFeature(feature)

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'lettertekenaanduidingen.qml'))

    def add_maatvoeringen_to_layers(self, vlakken, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Maatvoeringen', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('omvang_naam', QVariant.String),
            QgsField('omvang_waarde', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String)
        ])
        layer.updateFields()

        for vlak in vlakken:
            geometry = shape(vlak.get('geometrie'))
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))

            properties = {
                'id': vlak.get('id', None),
                'naam': vlak.get('naam', None),
                'omvang_naam': vlak.get('omvang', [{}])[0].get('naam', None),
                'omvang_waarde': vlak.get('omvang', [{}])[0].get('waarde', None),
                'verwijzingNaarTekst': vlak.get('verwijzingNaarTekst', None)
            }

            feature.setAttributes(
                [properties['id'], properties['naam'], properties['omvang_naam'], properties['omvang_waarde'],
                 properties['verwijzingNaarTekst']])
            provider.addFeature(feature)

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'maatvoeringen.qml'))

    def add_figuren_to_layers(self, vlakken, group):
        layer = QgsVectorLayer('LineString?crs=EPSG:28992', 'Figuren', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('artikelnummers', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String),
            QgsField('labelInfo', QVariant.String),
            QgsField('illustratie_href', QVariant.String),
            QgsField('illustratie_type', QVariant.String),
            QgsField('illustratie_naam', QVariant.String),
            QgsField('illustratie_legendanaam', QVariant.String)
        ])
        layer.updateFields()

        for vlak in vlakken:
            geometry = shape(vlak.get('geometrie'))
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))

            properties = {
                'id': vlak.get('id', None),
                'naam': vlak.get('naam', None),
                'artikelnummers': ', '.join(map(str, vlak.get('artikelnummers', []))),
                'verwijzingNaarTekst': ', '.join(vlak.get('verwijzingNaarTekst', [])),
                'labelInfo': vlak.get('labelInfo', None),
                'illustratie_href': vlak.get('illustratie', {}).get('href', None),
                'illustratie_type': vlak.get('illustratie', {}).get('type', None),
                'illustratie_naam': vlak.get('illustratie', {}).get('naam', None),
                'illustratie_legendanaam': vlak.get('illustratie', {}).get('legendanaam', None)
            }

            feature.setAttributes(
                [properties['id'], properties['naam'], properties['artikelnummers'], properties['verwijzingNaarTekst'],
                 properties['labelInfo'], properties['illustratie_href'], properties['illustratie_type'],
                 properties['illustratie_naam'], properties['illustratie_legendanaam']])
            provider.addFeature(feature)

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'figuren.qml'))

    def add_gebiedsaanduidingen_to_layers(self, vlakken, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Gebiedsaanduidingen', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('gebiedsaanduidinggroep', QVariant.String),
            QgsField('artikelnummers', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String),
            QgsField('labelInfo', QVariant.String),
            QgsField('bestemmingsfuncties_bestemmingsfunctie', QVariant.String),
            QgsField('bestemmingsfuncties_functieniveau', QVariant.String)
        ])
        layer.updateFields()

        for vlak in vlakken:
            geometry = shape(vlak.get('geometrie'))
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))

            properties = {
                'id': vlak.get('id', None),
                'naam': vlak.get('naam', None),
                'gebiedsaanduidinggroep': vlak.get('gebiedsaanduidinggroep', None),
                'artikelnummers': ', '.join(map(str, vlak.get('artikelnummers', []))),
                'verwijzingNaarTekst': ', '.join(vlak.get('verwijzingNaarTekst', [])),
                'labelInfo': vlak.get('labelInfo', None),
                'bestemmingsfuncties_bestemmingsfunctie': ', '.join(
                    f"{bf['bestemmingsfunctie']}" for bf in vlak.get('bestemmingsfuncties', [])),
                'bestemmingsfuncties_functieniveau': ', '.join(
                    f"{bf['functieniveau']}" for bf in vlak.get('bestemmingsfuncties', []))
            }

            feature.setAttributes([properties['id'], properties['naam'], properties['gebiedsaanduidinggroep'],
                                   properties['artikelnummers'], properties['verwijzingNaarTekst'],
                                   properties['labelInfo'], properties['bestemmingsfuncties_bestemmingsfunctie'],
                                   properties['bestemmingsfuncties_functieniveau']])
            provider.addFeature(feature)

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'gebiedsaanduidingen.qml'))

    def add_structuurvisiegebieden_to_layers(self, items, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Structuurvisiegebieden', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('thema', QVariant.String),
            QgsField('beleid_belang', QVariant.String),
            QgsField('beleid_rol', QVariant.String),
            QgsField('beleid_instrument', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String),
            QgsField('illustraties_href', QVariant.String),
            QgsField('illustraties_type', QVariant.String),
            QgsField('illustraties_naam', QVariant.String),
            QgsField('illustraties_legendanaam', QVariant.String),
            QgsField('cartografieInfo_kaartnummer', QVariant.Int),
            QgsField('cartografieInfo_kaartnaam', QVariant.String),
            QgsField('cartografieInfo_symboolCode', QVariant.String),
            QgsField('relatiesMetExternePlannen_naam', QVariant.String),
            QgsField('relatiesMetExternePlannen_id', QVariant.String),
            QgsField('relatiesMetExternePlannen_href', QVariant.String)
        ])
        layer.updateFields()

        for item in items:
            geometries = item.get('geometrie', [])
            for geom in geometries:
                geometry = shape(geom)
                if not isinstance(geometry, (Polygon, MultiPolygon)):
                    continue

                feature = QgsFeature()
                feature.setGeometry(
                    QgsGeometry.fromPolygonXY([[QgsPointXY(*coord) for coord in geometry.exterior.coords]]))

                beleid_list = item.get('beleid', [])
                illustraties_list = item.get('illustraties', [])
                cartografieInfo_list = item.get('cartografieInfo', [])
                relaties_list = item.get('relatiesMetExternePlannen', {}).get('tenGevolgeVan', [])

                beleid = beleid_list[0] if beleid_list else {}
                illustraties = illustraties_list[0] if illustraties_list else {}
                cartografieInfo = cartografieInfo_list[0] if cartografieInfo_list else {}
                relaties = relaties_list[0] if relaties_list else {}

                try:
                    properties = {
                        'id': item.get('id', None),
                        'naam': item.get('naam', None),
                        'thema': ', '.join(item.get('thema', [])),
                        'beleid_belang': beleid.get('belang', None),
                        'beleid_rol': beleid.get('rol', None),
                        'beleid_instrument': beleid.get('instrument', None),
                        'verwijzingNaarTekst': ', '.join(item.get('verwijzingNaarTekst', [])),
                        'illustraties_href': illustraties.get('href', None),
                        'illustraties_type': illustraties.get('type', None),
                        'illustraties_naam': illustraties.get('naam', None),
                        'illustraties_legendanaam': illustraties.get('legendanaam', None),
                        'cartografieInfo_kaartnummer': cartografieInfo.get('kaartnummer', None),
                        'cartografieInfo_kaartnaam': cartografieInfo.get('kaartnaam', None),
                        'cartografieInfo_symboolCode': cartografieInfo.get('symboolCode', None),
                        'relatiesMetExternePlannen_naam': relaties.get('naam', None),
                        'relatiesMetExternePlannen_id': relaties.get('id', None),
                        'relatiesMetExternePlannen_href': relaties.get('href', None)
                    }

                    feature.setAttributes([properties['id'], properties['naam'], properties['thema'],
                                           properties['beleid_belang'], properties['beleid_rol'],
                                           properties['beleid_instrument'],
                                           properties['verwijzingNaarTekst'], properties['illustraties_href'],
                                           properties['illustraties_type'], properties['illustraties_naam'],
                                           properties['illustraties_legendanaam'],
                                           properties['cartografieInfo_kaartnummer'],
                                           properties['cartografieInfo_kaartnaam'],
                                           properties['cartografieInfo_symboolCode'],
                                           properties['relatiesMetExternePlannen_naam'],
                                           properties['relatiesMetExternePlannen_id'],
                                           properties['relatiesMetExternePlannen_href']])
                    provider.addFeature(feature)
                except Exception as e:
                    print(f"Error processing feature: {e}")
                    print(f"Item: {item}")
                    print(f"Beleid: {beleid}")
                    print(f"Illustraties: {illustraties}")
                    print(f"CartografieInfo: {cartografieInfo}")
                    print(f"Relaties: {relaties}")

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'structuurvisiegebieden.qml'))

    def add_structuurvisiecomplexen_to_layers(self, items, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Structuurvisiecomplexen', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('thema', QVariant.String),
            QgsField('beleid_belang', QVariant.String),
            QgsField('beleid_rol', QVariant.String),
            QgsField('beleid_instrument', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String),
            QgsField('illustraties_href', QVariant.String),
            QgsField('illustraties_type', QVariant.String),
            QgsField('illustraties_naam', QVariant.String),
            QgsField('illustraties_legendanaam', QVariant.String),
            QgsField('cartografieInfo_kaartnummer', QVariant.Int),
            QgsField('cartografieInfo_kaartnaam', QVariant.String),
            QgsField('cartografieInfo_symboolCode', QVariant.String),
            QgsField('relatiesMetExternePlannen_naam', QVariant.String),
            QgsField('relatiesMetExternePlannen_id', QVariant.String),
            QgsField('relatiesMetExternePlannen_href', QVariant.String)
        ])
        layer.updateFields()

        for item in items:
            geometries = item.get('geometrie', [])
            for geom in geometries:
                geometry = shape(geom)
                if not isinstance(geometry, (Polygon, MultiPolygon)):
                    continue

                feature = QgsFeature()
                feature.setGeometry(
                    QgsGeometry.fromPolygonXY([[QgsPointXY(*coord) for coord in geometry.exterior.coords]]))

                beleid_list = item.get('beleid', [])
                illustraties_list = item.get('illustraties', [])
                cartografieInfo_list = item.get('cartografieInfo', [])
                relaties_list = item.get('relatiesMetExternePlannen', {}).get('tenGevolgeVan', [])

                beleid = beleid_list[0] if beleid_list else {}
                illustraties = illustraties_list[0] if illustraties_list else {}
                cartografieInfo = cartografieInfo_list[0] if cartografieInfo_list else {}
                relaties = relaties_list[0] if relaties_list else {}

                try:
                    properties = {
                        'id': item.get('id', None),
                        'naam': item.get('naam', None),
                        'thema': item.get('thema', None),
                        'beleid_belang': beleid.get('belang', None),
                        'beleid_rol': beleid.get('rol', None),
                        'beleid_instrument': beleid.get('instrument', None),
                        'verwijzingNaarTekst': ', '.join(item.get('verwijzingNaarTekst', [])),
                        'illustraties_href': illustraties.get('href', None),
                        'illustraties_type': illustraties.get('type', None),
                        'illustraties_naam': illustraties.get('naam', None),
                        'illustraties_legendanaam': illustraties.get('legendanaam', None),
                        'cartografieInfo_kaartnummer': cartografieInfo.get('kaartnummer', None),
                        'cartografieInfo_kaartnaam': cartografieInfo.get('kaartnaam', None),
                        'cartografieInfo_symboolCode': cartografieInfo.get('symboolCode', None),
                        'relatiesMetExternePlannen_naam': relaties.get('naam', None),
                        'relatiesMetExternePlannen_id': relaties.get('id', None),
                        'relatiesMetExternePlannen_href': relaties.get('href', None)
                    }

                    feature.setAttributes([properties['id'], properties['naam'], properties['thema'],
                                           properties['beleid_belang'], properties['beleid_rol'],
                                           properties['beleid_instrument'],
                                           properties['verwijzingNaarTekst'], properties['illustraties_href'],
                                           properties['illustraties_type'], properties['illustraties_naam'],
                                           properties['illustraties_legendanaam'],
                                           properties['cartografieInfo_kaartnummer'],
                                           properties['cartografieInfo_kaartnaam'],
                                           properties['cartografieInfo_symboolCode'],
                                           properties['relatiesMetExternePlannen_naam'],
                                           properties['relatiesMetExternePlannen_id'],
                                           properties['relatiesMetExternePlannen_href']])
                    provider.addFeature(feature)
                except Exception as e:
                    print(f"Error processing feature: {e}")
                    print(f"Item: {item}")
                    print(f"Beleid: {beleid}")
                    print(f"Illustraties: {illustraties}")
                    print(f"CartografieInfo: {cartografieInfo}")
                    print(f"Relaties: {relaties}")

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'structuurvisiecomplexen.qml'))

    def add_besluitvlakken_to_layers(self, items, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Besluitvlakken', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('thema', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String),
            QgsField('illustraties_href', QVariant.String),
            QgsField('illustraties_type', QVariant.String),
            QgsField('illustraties_naam', QVariant.String),
            QgsField('illustraties_legendanaam', QVariant.String),
            QgsField('cartografieInfo_kaartnummer', QVariant.Int),
            QgsField('cartografieInfo_kaartnaam', QVariant.String),
            QgsField('cartografieInfo_symboolCode', QVariant.String),
            QgsField('besluitsubvlakken_id', QVariant.String),
            QgsField('besluitsubvlakken_naam', QVariant.String),
            QgsField('besluitsubvlakken_href', QVariant.String)
        ])
        layer.updateFields()

        for item in items:
            geometries = item.get('geometrie', [])
            for geom in geometries:
                geometry = shape(geom)
                if not isinstance(geometry, (Polygon, MultiPolygon)):
                    continue

                feature = QgsFeature()
                feature.setGeometry(
                    QgsGeometry.fromPolygonXY([[QgsPointXY(*coord) for coord in geometry.exterior.coords]]))

                illustraties_list = item.get('illustraties', [])
                cartografieInfo_list = item.get('cartografieInfo', [])
                besluitsubvlakken_list = item.get('_embedded', {}).get('besluitsubvlakken', [])

                illustraties = illustraties_list[0] if illustraties_list else {}
                cartografieInfo = cartografieInfo_list[0] if cartografieInfo_list else {}
                besluitsubvlakken = besluitsubvlakken_list[0] if besluitsubvlakken_list else {}

                try:
                    properties = {
                        'id': item.get('id', None),
                        'naam': item.get('naam', None),
                        'thema': ', '.join(item.get('thema', [])),
                        'verwijzingNaarTekst': ', '.join(item.get('verwijzingNaarTekst', [])),
                        'illustraties_href': illustraties.get('href', None),
                        'illustraties_type': illustraties.get('type', None),
                        'illustraties_naam': illustraties.get('naam', None),
                        'illustraties_legendanaam': illustraties.get('legendanaam', None),
                        'cartografieInfo_kaartnummer': cartografieInfo.get('kaartnummer', None),
                        'cartografieInfo_kaartnaam': cartografieInfo.get('kaartnaam', None),
                        'cartografieInfo_symboolCode': cartografieInfo.get('symboolCode', None),
                        'besluitsubvlakken_id': besluitsubvlakken.get('id', None),
                        'besluitsubvlakken_naam': besluitsubvlakken.get('naam', None),
                        'besluitsubvlakken_href': besluitsubvlakken.get('_links', {}).get('self', {}).get('href', None)
                    }

                    feature.setAttributes([properties['id'], properties['naam'], properties['thema'],
                                           properties['verwijzingNaarTekst'], properties['illustraties_href'],
                                           properties['illustraties_type'], properties['illustraties_naam'],
                                           properties['illustraties_legendanaam'],
                                           properties['cartografieInfo_kaartnummer'],
                                           properties['cartografieInfo_kaartnaam'],
                                           properties['cartografieInfo_symboolCode'],
                                           properties['besluitsubvlakken_id'], properties['besluitsubvlakken_naam'],
                                           properties['besluitsubvlakken_href']])
                    provider.addFeature(feature)
                except Exception as e:
                    print(f"Error processing feature: {e}")
                    print(f"Item: {item}")
                    print(f"Illustraties: {illustraties}")
                    print(f"CartografieInfo: {cartografieInfo}")
                    print(f"Besluitsubvlakken: {besluitsubvlakken}")

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'besluitvlakken.qml'))

    def add_besluitsubvlakken_to_layers(self, vlakken, group):
        layer = QgsVectorLayer('Polygon?crs=EPSG:28992', 'Besluitsubvlakken', 'memory')
        provider = layer.dataProvider()

        provider.addAttributes([
            QgsField('id', QVariant.String),
            QgsField('naam', QVariant.String),
            QgsField('thema', QVariant.String),
            QgsField('verwijzingNaarTekst', QVariant.String),
            QgsField('illustraties_href', QVariant.String),
            QgsField('illustraties_type', QVariant.String),
            QgsField('illustraties_naam', QVariant.String),
            QgsField('illustraties_legendanaam', QVariant.String),
            QgsField('cartografieInfo_kaartnummer', QVariant.Int),
            QgsField('cartografieInfo_kaartnaam', QVariant.String),
            QgsField('cartografieInfo_symboolCode', QVariant.String)
        ])
        layer.updateFields()

        for vlak in vlakken:
            geometry = shape(vlak.get('geometrie'))
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt(geometry.wkt))

            properties = {
                'id': vlak.get('id', None),
                'naam': vlak.get('naam', None),
                'thema': ', '.join(vlak.get('thema', [])),
                'verwijzingNaarTekst': ', '.join(vlak.get('verwijzingNaarTekst', [])),
                'illustraties_href': vlak.get('illustraties', [{}])[0].get('href', None),
                'illustraties_type': vlak.get('illustraties', [{}])[0].get('type', None),
                'illustraties_naam': vlak.get('illustraties', [{}])[0].get('naam', None),
                'illustraties_legendanaam': vlak.get('illustraties', [{}])[0].get('legendanaam', None),
                'cartografieInfo_kaartnummer': vlak.get('cartografieInfo', [{}])[0].get('kaartnummer', None),
                'cartografieInfo_kaartnaam': vlak.get('cartografieInfo', [{}])[0].get('kaartnaam', None),
                'cartografieInfo_symboolCode': vlak.get('cartografieInfo', [{}])[0].get('symboolCode', None)
            }

            feature.setAttributes(
                [properties['id'], properties['naam'], properties['thema'], properties['verwijzingNaarTekst'],
                 properties['illustraties_href'], properties['illustraties_type'], properties['illustraties_naam'],
                 properties['illustraties_legendanaam'], properties['cartografieInfo_kaartnummer'],
                 properties['cartografieInfo_kaartnaam'], properties['cartografieInfo_symboolCode']])
            provider.addFeature(feature)

        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        layer.loadNamedStyle(os.path.join(QML_PATH, 'besluitvakken.qml'))

    def get_styling_order(self, hoofdgroep):
        hoofdgroep = hoofdgroep.lower()
        orders = {
            'null': 8,
            'overig': 7,
            'verkeer': 6,
            'natuur': 5,
            'agrarisch': 4,
            'agrarisch met waarden': 3,
            'woongebied': 2,
            'wonen': 1,
            'bos': 1
        }
        return orders.get(hoofdgroep, 0)

    def unload(self):
        # Remove the toolbar
        self.iface.removeToolBarIcon(self.main_action)
        del self.toolbar

        # Remove the group and all its layers
        root = QgsProject.instance().layerTreeRoot()
        for group in root.children():
            if group.name() == 'Ruimtelijke Plannen':
                root.removeChildNode(group)
                break

    def show_search_dialog(self):
        dialog = SearchDialog()
        if dialog.exec_():
            imro_code = dialog.get_imro_code()
            if imro_code:
                self.fetch_and_import_plan_by_imro_code(imro_code)


    def fetch_and_import_plan_by_imro_code(self, imro_code):
        url = f'https://ruimte.omgevingswet.overheid.nl/ruimtelijke-plannen/api/opvragen/v4/plannen/{imro_code}'
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'Accept-Crs': 'epsg:28992'
        }

        try:
            logging.info(f"Fetching plan data for IMRO Code: {imro_code}")
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            plan_data = response.json()
            if not plan_data:
                logging.error(f"No data returned for IMRO Code: {imro_code}")
                QMessageBox.warning(None, "Error", f"No data returned for IMRO Code {imro_code}.")
                return

            # Ensure that 'geometrie' is in the response data
            if 'geometrie' not in plan_data:
                logging.warning(f"Geometrie not found for IMRO Code: {imro_code}, querying plan geometry separately.")
                plan_data = self.query_plan_geometry(imro_code)

            if not plan_data:
                QMessageBox.warning(None, "Error", f"Failed to retrieve plan data or geometry for IMRO Code {imro_code}.")
                return

            # Process the plan data
            group_name = f"{imro_code} - {plan_data.get('naam', 'Unnamed Plan')}"

            # Check if the group already exists
            root = QgsProject.instance().layerTreeRoot()
            existing_group = root.findGroup(group_name)

            if existing_group:
                logging.warning(f"Group {group_name} already exists. Import process stopped.")
                QMessageBox.warning(None, "Warning",
                                    f"The group '{group_name}' already exists. Import process stopped.")
                return

            group = QgsProject.instance().layerTreeRoot().insertGroup(0, group_name)

            plan_layer_group = group.addGroup("Plan")
            self.add_plan_to_layers(plan_data, plan_data.get('naam', 'Unnamed Plan'), plan_layer_group)

            # Safeguard for all query-and-add operations
            query_add_methods = [
                self.query_and_add_bouwvlakken,
                self.query_and_add_functieaanduidingen,
                self.query_and_add_bouwaanduidingen,
                self.query_and_add_lettertekenaanduidingen,
                self.query_and_add_maatvoeringen,
                self.query_and_add_figuren,
                self.query_and_add_gebiedsaanduidingen,
                self.query_and_add_structuurvisiegebieden,
                self.query_and_add_structuurvisiecomplexen,
                self.query_and_add_besluitvlakken,
                self.query_and_add_besluitsubvlakken,
                self.query_and_add_bekendmakingen,
                self.query_and_add_bestemmingsvlakken
            ]

            for method in query_add_methods:
                try:
                    method(imro_code, group)
                except Exception as err:
                    logging.error(f"Error occurred while adding layers with method {method.__name__}: {err}")
                    QMessageBox.warning(None, "Error",
                                        f"Error occurred while adding layers with method {method.__name__}: {err}")

        except requests.exceptions.HTTPError as http_err:
            if response.status_code == 404:
                logging.error(f"404 Error: Plan not found for IMRO Code: {imro_code}")
                QMessageBox.warning(None, "Error",
                                    f"Plan not found or no geometry was found for IMRO Code {imro_code}.")
            else:
                logging.error(f"HTTP error occurred for IMRO Code: {imro_code} - {http_err}")
                QMessageBox.warning(None, "Error",
                                    f"HTTP error occurred: {http_err} - Response content: {response.content.decode('utf-8', errors='ignore')}")
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Request error occurred for IMRO Code: {imro_code} - {req_err}")
            QMessageBox.warning(None, "Error", f"Request error occurred: {req_err}")
        except TypeError as type_err:
            logging.error(f"TypeError occurred, possibly due to NoneType: {type_err}")
            QMessageBox.warning(None, "Error", f"TypeError occurred: {type_err}")
        except RuntimeError as run_err:
            logging.error(f"RuntimeError occurred, possibly due to a deleted QgsLayerTreeGroup: {run_err}")
            QMessageBox.warning(None, "Error", f"RuntimeError occurred, possibly due to a deleted group: {run_err}")
        except Exception as err:
            logging.error(f"An unexpected error occurred for IMRO Code: {imro_code} - {err}")
            QMessageBox.warning(None, "Error", f"An unexpected error occurred: {err}")



class SettingsDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('API Settings')
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.api_key_input = QLineEdit()
        self.layout.addWidget(QLabel('API Key:'))
        self.layout.addWidget(self.api_key_input)

        self.method_choice = QComboBox()
        self.method_choice.addItems(["Polygon Click", "Point Click"])
        self.layout.addWidget(QLabel('Choose Data Collection Method:'))
        self.layout.addWidget(self.method_choice)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)

    def set_api_key(self, api_key):
        self.api_key_input.setText(api_key)

    def get_api_key(self):
        return self.api_key_input.text()

    def get_method_choice(self):
        return self.method_choice.currentText()


class PlanTypeDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Kies een ruimtelijk plan')
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        layout = QVBoxLayout()
        splitter = QSplitter(Qt.Horizontal)

        self.plan_type_widget = QListWidget()
        self.plan_type_widget.setSelectionMode(QListWidget.MultiSelection)
        for plan_type in PLAN_TYPES:
            item = QListWidgetItem(plan_type)
            item.setCheckState(Qt.Unchecked)
            self.plan_type_widget.addItem(item)

        self.status_widget = QListWidget()
        self.status_widget.setSelectionMode(QListWidget.MultiSelection)
        for status in PLAN_STATUSES:
            item = QListWidgetItem(status)
            item.setCheckState(Qt.Unchecked)
            self.status_widget.addItem(item)

        splitter.addWidget(self.plan_type_widget)
        splitter.addWidget(self.status_widget)

        layout.addWidget(splitter)

        button_layout = QHBoxLayout()
        ok_button = QPushButton('OK')
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton('Cancel')
        cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def get_selected_plan_types(self):
        return [item.text() for item in self.plan_type_widget.findItems('*', Qt.MatchWildcard) if
                item.checkState() == Qt.Checked]

    def get_selected_statuses(self):
        return [item.text() for item in self.status_widget.findItems('*', Qt.MatchWildcard) if
                item.checkState() == Qt.Checked]


class SearchDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Search by IMRO Code')
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.imro_code_input = QLineEdit()
        self.layout.addWidget(QLabel('IMRO Code:'))
        self.layout.addWidget(self.imro_code_input)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)

    def get_imro_code(self):
        return self.imro_code_input.text()


def classFactory(iface):
    from .RuimtelijkePlannen import RuimtelijkePlannen
    return RuimtelijkePlannen(iface)
