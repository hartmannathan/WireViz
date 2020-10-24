#!/usr/bin/env python
# -*- coding: utf-8 -*-

from graphviz import Graph
from collections import Counter
from typing import List, Union
from pathlib import Path
from itertools import zip_longest
import re

from wireviz import wv_colors, __version__, APP_NAME, APP_URL
from wireviz.DataClasses import Connector, Cable
from wireviz.wv_colors import get_color_hex
from wireviz.wv_gv_html import nested_html_table, html_colorbar, html_image, \
    html_caption, remove_links, html_line_breaks
from wireviz.wv_bom import manufacturer_info_field, component_table_entry, \
    get_additional_component_table, bom_list, generate_bom
from wireviz.wv_html import generate_html_output
from wireviz.wv_helper import awg_equiv, mm2_equiv, tuplelist2tsv, flatten2d, \
    open_file_read, open_file_write

arrows = ['<--','<->','-->','<==','<=>','==>']

class Harness:

    def __init__(self):
        self.color_mode = 'SHORT'
        self.mini_bom_mode = True
        self.connectors = {}
        self.cables = {}
        self.mates_pin = []
        self.mates_component = []
        self._bom = []  # Internal Cache for generated bom
        self.additional_bom_items = []

    def add_connector(self, name: str, *args, **kwargs) -> None:
        self.connectors[name] = Connector(name, *args, **kwargs)

    def add_cable(self, name: str, *args, **kwargs) -> None:
        self.cables[name] = Cable(name, *args, **kwargs)

    def add_mate_pin(self, *args, **kwargs) -> None:
        self.mates_pin.append(MatePin(*args, **kwargs))

    def add_mate_component(self, *args, **kwargs) -> None:
        self.mates_component.append(MateComponent(*args, **kwargs))

    def add_bom_item(self, item: dict) -> None:
        self.additional_bom_items.append(item)

    def connect(self, from_name: str, from_pin: (int, str), via_name: str, via_wire: (int, str), to_name: str, to_pin: (int, str)) -> None:
        # check from and to connectors
        for (name, pin) in zip([from_name, to_name], [from_pin, to_pin]):
            if name is not None and name in self.connectors:
                connector = self.connectors[name]
                # check if provided name is ambiguous
                if pin in connector.pins and pin in connector.pinlabels:
                    if connector.pins.index(pin) != connector.pinlabels.index(pin):
                        raise Exception(f'{name}:{pin} is defined both in pinlabels and pins, for different pins.')
                    # TODO: Maybe issue a warning if present in both lists but referencing the same pin?
                if pin in connector.pinlabels:
                    if connector.pinlabels.count(pin) > 1:
                        raise Exception(f'{name}:{pin} is defined more than once.')
                    index = connector.pinlabels.index(pin)
                    pin = connector.pins[index] # map pin name to pin number
                    if name == from_name:
                        from_pin = pin
                    if name == to_name:
                        to_pin = pin
                if not pin in connector.pins:
                    raise Exception(f'{name}:{pin} not found.')

        # check via cable
        if via_name in arrows:
            if '-' in via_name:
                self.mates[(from_name, from_pin, to_name, to_pin)] = via_name
            elif '=' in via_name:
                self.mates[(from_name, to_name)] = via_name
            print(self.mates)
        elif via_name in self.cables:
            cable = self.cables[via_name]
            # check if provided name is ambiguous
            if via_wire in cable.colors and via_wire in cable.wirelabels:
                if cable.colors.index(via_wire) != cable.wirelabels.index(via_wire):
                    raise Exception(f'{via_name}:{via_wire} is defined both in colors and wirelabels, for different wires.')
                # TODO: Maybe issue a warning if present in both lists but referencing the same wire?
            if via_wire in cable.colors:
                if cable.colors.count(via_wire) > 1:
                    raise Exception(f'{via_name}:{via_wire} is used for more than one wire.')
                via_wire = cable.colors.index(via_wire) + 1  # list index starts at 0, wire IDs start at 1
            elif via_wire in cable.wirelabels:
                if cable.wirelabels.count(via_wire) > 1:
                    raise Exception(f'{via_name}:{via_wire} is used for more than one wire.')
                via_wire = cable.wirelabels.index(via_wire) + 1  # list index starts at 0, wire IDs start at 1

        self.cables[via_name].connect(from_name, from_pin, via_wire, to_name, to_pin)
        if from_name in self.connectors:
            self.connectors[from_name].activate_pin(from_pin)
        if to_name in self.connectors:
            self.connectors[to_name].activate_pin(to_pin)

    def create_graph(self) -> Graph:
        dot = Graph()
        dot.body.append(f'// Graph generated by {APP_NAME} {__version__}')
        dot.body.append(f'// {APP_URL}')
        font = 'arial'
        dot.attr('graph', rankdir='LR',
                 ranksep='2',
                 bgcolor='white',
                 nodesep='0.33',
                 fontname=font)
        dot.attr('node', shape='record',
                 style='filled',
                 fillcolor='white',
                 fontname=font)
        dot.attr('edge', style='bold',
                 fontname=font)

        # prepare ports on connectors depending on which side they will connect
        for _, cable in self.cables.items():
            for connection_color in cable.connections:
                if connection_color.from_port is not None:  # connect to left
                    self.connectors[connection_color.from_name].ports_right = True
                if connection_color.to_port is not None:  # connect to right
                    self.connectors[connection_color.to_name].ports_left = True
        for mate in self.mates_pin:
            self.connectors[mate.from_name].ports_right = True
            self.connectors[mate.from_name].activate_pin(mate.from_port)
            self.connectors[mate.to_name].ports_left = True
            self.connectors[mate.to_name].activate_pin(mate.to_port)

        for connector in self.connectors.values():

            html = []

            rows = [[remove_links(connector.name) if connector.show_name else None],
                    [f'P/N: {remove_links(connector.pn)}' if connector.pn else None,
                     html_line_breaks(manufacturer_info_field(connector.manufacturer, connector.mpn))],
                    [html_line_breaks(connector.type),
                     html_line_breaks(connector.subtype),
                     f'{connector.pincount}-pin' if connector.show_pincount else None,
                     connector.color, html_colorbar(connector.color)],
                    '<!-- connector table -->' if connector.style != 'simple' else None,
                    [html_image(connector.image)],
                    [html_caption(connector.image)]]
            rows.extend(get_additional_component_table(self, connector))
            rows.append([html_line_breaks(connector.notes)])
            html.extend(nested_html_table(rows))

            if connector.style != 'simple':
                pinhtml = []
                pinhtml.append('<table border="0" cellspacing="0" cellpadding="3" cellborder="1">')

                for pin, pinlabel, pincolor in zip_longest(connector.pins, connector.pinlabels, connector.pincolors):
                    if connector.hide_disconnected_pins and not connector.visible_pins.get(pin, False):
                        continue
                    pinhtml.append('   <tr>')
                    if connector.ports_left:
                        pinhtml.append(f'    <td port="p{pin}l">{pin}</td>')
                    if pinlabel:
                        pinhtml.append(f'    <td>{pinlabel}</td>')
                    if connector.pincolors:
                        if pincolor in wv_colors._color_hex.keys():
                            pinhtml.append(f'    <td sides="tbl">{pincolor}</td>')
                            pinhtml.append( '    <td sides="tbr">')
                            pinhtml.append( '     <table border="0" cellborder="1"><tr>')
                            pinhtml.append(f'      <td bgcolor="{wv_colors.translate_color(pincolor, "HEX")}" width="8" height="8" fixedsize="true"></td>')
                            pinhtml.append( '     </tr></table>')
                            pinhtml.append( '    </td>')
                        else:
                            pinhtml.append( '    <td colspan="2"></td>')

                    if connector.ports_right:
                        pinhtml.append(f'    <td port="p{pin}r">{pin}</td>')
                    pinhtml.append('   </tr>')

                pinhtml.append('  </table>')

                html = [row.replace('<!-- connector table -->', '\n'.join(pinhtml)) for row in html]

            html = '\n'.join(html)
            dot.node(connector.name, label=f'<\n{html}\n>', shape='none', margin='0', style='filled', fillcolor='white')

            if len(connector.loops) > 0:
                dot.attr('edge', color='#000000:#ffffff:#000000')
                if connector.ports_left:
                    loop_side = 'l'
                    loop_dir = 'w'
                elif connector.ports_right:
                    loop_side = 'r'
                    loop_dir = 'e'
                else:
                    raise Exception('No side for loops')
                for loop in connector.loops:
                    dot.edge(f'{connector.name}:p{loop[0]}{loop_side}:{loop_dir}',
                             f'{connector.name}:p{loop[1]}{loop_side}:{loop_dir}')


        # determine if there are double- or triple-colored wires in the harness;
        # if so, pad single-color wires to make all wires of equal thickness
        pad = any(len(colorstr) > 2 for cable in self.cables.values() for colorstr in cable.colors)

        for cable in self.cables.values():

            html = []

            awg_fmt = ''
            if cable.show_equiv:
                # Only convert units we actually know about, i.e. currently
                # mm2 and awg --- other units _are_ technically allowed,
                # and passed through as-is.
                if cable.gauge_unit =='mm\u00B2':
                    awg_fmt = f' ({awg_equiv(cable.gauge)} AWG)'
                elif cable.gauge_unit.upper() == 'AWG':
                    awg_fmt = f' ({mm2_equiv(cable.gauge)} mm\u00B2)'

            rows = [[remove_links(cable.name) if cable.show_name else None],
                    [f'P/N: {remove_links(cable.pn)}' if (cable.pn and not isinstance(cable.pn, list)) else None,
                     html_line_breaks(manufacturer_info_field(
                        cable.manufacturer if not isinstance(cable.manufacturer, list) else None,
                        cable.mpn if not isinstance(cable.mpn, list) else None))],
                    [html_line_breaks(cable.type),
                     f'{cable.wirecount}x' if cable.show_wirecount else None,
                     f'{cable.gauge} {cable.gauge_unit}{awg_fmt}' if cable.gauge else None,
                     '+ S' if cable.shield else None,
                     f'{cable.length} m' if cable.length > 0 else None,
                     cable.color, html_colorbar(cable.color)],
                    '<!-- wire table -->',
                    [html_image(cable.image)],
                    [html_caption(cable.image)]]

            rows.extend(get_additional_component_table(self, cable))
            rows.append([html_line_breaks(cable.notes)])
            html.extend(nested_html_table(rows))

            wirehtml = []
            wirehtml.append('<table border="0" cellspacing="0" cellborder="0">')  # conductor table
            wirehtml.append('   <tr><td>&nbsp;</td></tr>')

            for i, (connection_color, wirelabel) in enumerate(zip_longest(cable.colors, cable.wirelabels), 1):
                wirehtml.append('   <tr>')
                wirehtml.append(f'    <td><!-- {i}_in --></td>')
                wirehtml.append(f'    <td>')

                wireinfo = []
                if cable.show_wirenumbers:
                    wireinfo.append(str(i))
                colorstr = wv_colors.translate_color(connection_color, self.color_mode)
                if colorstr:
                    wireinfo.append(colorstr)
                if cable.wirelabels:
                    wireinfo.append(wirelabel if wirelabel is not None else '')
                wirehtml.append(f'     {":".join(wireinfo)}')

                wirehtml.append(f'    </td>')
                wirehtml.append(f'    <td><!-- {i}_out --></td>')
                wirehtml.append('   </tr>')

                bgcolors = ['#000000'] + get_color_hex(connection_color, pad=pad) + ['#000000']
                wirehtml.append(f'   <tr>')
                wirehtml.append(f'    <td colspan="3" border="0" cellspacing="0" cellpadding="0" port="w{i}" height="{(2 * len(bgcolors))}">')
                wirehtml.append('     <table cellspacing="0" cellborder="0" border="0">')
                for j, bgcolor in enumerate(bgcolors[::-1]):  # Reverse to match the curved wires when more than 2 colors
                    wirehtml.append(f'      <tr><td colspan="3" cellpadding="0" height="2" bgcolor="{bgcolor if bgcolor != "" else wv_colors.default_color}" border="0"></td></tr>')
                wirehtml.append('     </table>')
                wirehtml.append('    </td>')
                wirehtml.append('   </tr>')
                if cable.category == 'bundle':  # for bundles individual wires can have part information
                    # create a list of wire parameters
                    wireidentification = []
                    if isinstance(cable.pn, list):
                        wireidentification.append(f'P/N: {remove_links(cable.pn[i - 1])}')
                    manufacturer_info = manufacturer_info_field(
                        cable.manufacturer[i - 1] if isinstance(cable.manufacturer, list) else None,
                        cable.mpn[i - 1] if isinstance(cable.mpn, list) else None)
                    if manufacturer_info:
                        wireidentification.append(html_line_breaks(manufacturer_info))
                    # print parameters into a table row under the wire
                    if len(wireidentification) > 0 :
                        wirehtml.append('   <tr><td colspan="3">')
                        wirehtml.append('    <table border="0" cellspacing="0" cellborder="0"><tr>')
                        for attrib in wireidentification:
                            wirehtml.append(f'     <td>{attrib}</td>')
                        wirehtml.append('    </tr></table>')
                        wirehtml.append('   </td></tr>')

            if cable.shield:
                wirehtml.append('   <tr><td>&nbsp;</td></tr>')  # spacer
                wirehtml.append('   <tr>')
                wirehtml.append('    <td><!-- s_in --></td>')
                wirehtml.append('    <td>Shield</td>')
                wirehtml.append('    <td><!-- s_out --></td>')
                wirehtml.append('   </tr>')
                if isinstance(cable.shield, str):
                    # shield is shown with specified color and black borders
                    shield_color_hex = wv_colors.get_color_hex(cable.shield)[0]
                    attributes = f'height="6" bgcolor="{shield_color_hex}" border="2" sides="tb"'
                else:
                    # shield is shown as a thin black wire
                    attributes = f'height="2" bgcolor="#000000" border="0"'
                wirehtml.append(f'   <tr><td colspan="3" cellpadding="0" {attributes} port="ws"></td></tr>')

            wirehtml.append('   <tr><td>&nbsp;</td></tr>')
            wirehtml.append('  </table>')

            html = [row.replace('<!-- wire table -->', '\n'.join(wirehtml)) for row in html]

            # connections
            for connection in cable.connections:
                if isinstance(connection.via_port, int):  # check if it's an actual wire and not a shield
                    dot.attr('edge', color=':'.join(['#000000'] + wv_colors.get_color_hex(cable.colors[connection.via_port - 1], pad=pad) + ['#000000']))
                else:  # it's a shield connection
                    # shield is shown with specified color and black borders, or as a thin black wire otherwise
                    dot.attr('edge', color=':'.join(['#000000', shield_color_hex, '#000000']) if isinstance(cable.shield, str) else '#000000')
                if connection.from_port is not None:  # connect to left
                    from_connector = self.connectors[connection.from_name]
                    from_port = f':p{connection.from_port}r' if from_connector.style != 'simple' else ''
                    code_left_1 = f'{connection.from_name}{from_port}:e'
                    code_left_2 = f'{cable.name}:w{connection.via_port}:w'
                    dot.edge(code_left_1, code_left_2)
                    if from_connector.show_name:
                        from_info = [str(connection.from_name), str(connection.from_port)]
                        if from_connector.pinlabels:
                            pinlabel = from_connector.pinlabels[from_connector.pins.index(connection.from_port)]
                            if pinlabel != '':
                                from_info.append(pinlabel)
                        from_string = ':'.join(from_info)
                    else:
                        from_string = ''
                    html = [row.replace(f'<!-- {connection.via_port}_in -->', from_string) for row in html]
                if connection.to_port is not None:  # connect to right
                    to_connector = self.connectors[connection.to_name]
                    code_right_1 = f'{cable.name}:w{connection.via_port}:e'
                    to_port = f':p{connection.to_port}l' if self.connectors[connection.to_name].style != 'simple' else ''
                    code_right_2 = f'{connection.to_name}{to_port}:w'
                    dot.edge(code_right_1, code_right_2)
                    if to_connector.show_name:
                        to_info = [str(connection.to_name), str(connection.to_port)]
                        if to_connector.pinlabels:
                            pinlabel = to_connector.pinlabels[to_connector.pins.index(connection.to_port)]
                            if pinlabel != '':
                                to_info.append(pinlabel)
                        to_string = ':'.join(to_info)
                    else:
                        to_string = ''
                    html = [row.replace(f'<!-- {connection.via_port}_out -->', to_string) for row in html]

            html = '\n'.join(html)
            dot.node(cable.name, label=f'<\n{html}\n>', shape='box',
                     style='filled,dashed' if cable.category == 'bundle' else '', margin='0', fillcolor='white')

        for mate in self.mates_pin:
            if mate.shape == '<--':
                dir = 'back'
            elif mate.shape == '-->':
                dir = 'forward'
            elif mate.shape == '<->':
                dir = 'both'
            dot.attr('edge', color='#000000', style='dashed', dir=dir)
            from_port = f':p{mate.from_port}r' if self.connectors[mate.from_name].style != 'simple' else ''
            code_from = f'{mate.from_name}{from_port}:e'
            to_port = f':p{mate.to_port}l' if self.connectors[mate.to_name].style != 'simple' else ''
            code_to = f'{mate.to_name}{to_port}:w'
            print(mate, '---', code_from, '---', code_to)
            dot.edge(code_from, code_to)

        return dot

    @property
    def png(self):
        from io import BytesIO
        graph = self.create_graph()
        data = BytesIO()
        data.write(graph.pipe(format='png'))
        data.seek(0)
        return data.read()

    @property
    def svg(self):
        from io import BytesIO
        graph = self.create_graph()
        data = BytesIO()
        data.write(graph.pipe(format='svg'))
        data.seek(0)
        return data.read()

    def output(self, filename: (str, Path), view: bool = False, cleanup: bool = True, fmt: tuple = ('pdf', )) -> None:
        # graphical output
        graph = self.create_graph()
        for f in fmt:
            graph.format = f
            graph.render(filename=filename, view=view, cleanup=cleanup)
        graph.save(filename=f'{filename}.gv')
        # bom output
        bomlist = bom_list(self.bom())
        with open_file_write(f'{filename}.bom.tsv') as file:
            file.write(tuplelist2tsv(bomlist))
        # HTML output
        generate_html_output(filename, bomlist)

    def bom(self):
        if not self._bom:
            self._bom = generate_bom(self)
        return self._bom
