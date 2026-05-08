{
  "patcher": {
    "fileversion": 1,
    "appversion": {
      "major": 8,
      "minor": 5,
      "revision": 5,
      "architecture": "x64",
      "modernui": 1
    },
    "classnamespace": "box",
    "rect": [
      100.0,
      100.0,
      720.0,
      480.0
    ],
    "bglocked": 0,
    "openinpresentation": 1,
    "default_fontsize": 12.0,
    "default_fontface": 0,
    "default_fontname": "Arial",
    "gridonopen": 1,
    "gridsize": [
      15.0,
      15.0
    ],
    "gridsnaponopen": 1,
    "objectsnaponopen": 1,
    "statusbarvisible": 2,
    "toolbarvisible": 1,
    "lefttoolbarpinned": 0,
    "toptoolbarpinned": 0,
    "righttoolbarpinned": 0,
    "bottomtoolbarpinned": 0,
    "toolbars_unpinned_last_save": 0,
    "tallnewobj": 0,
    "boxanimatetime": 200,
    "enablehscroll": 1,
    "enablevscroll": 1,
    "devicewidth": 0.0,
    "description": "AbletonFullControlTape \u00e2\u20ac\u201d OSC-controlled tape recorder for AbletonMCP. Listens on UDP/11003, replies on UDP/11004.",
    "digest": "",
    "tags": "",
    "style": "",
    "subpatcher_template": "",
    "showontab": 0,
    "assistshowspatchername": 0,
    "boxes": [
      {
        "box": {
          "id": "obj-thisdevice",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 3,
          "outlettype": [
            "",
            "",
            ""
          ],
          "patching_rect": [
            30.0,
            30.0,
            100.0,
            22.0
          ],
          "text": "live.thisdevice"
        }
      },
      {
        "box": {
          "id": "obj-udprecv",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 1,
          "outlettype": [
            ""
          ],
          "patching_rect": [
            30.0,
            70.0,
            130.0,
            22.0
          ],
          "text": "udpreceive 11003"
        }
      },
      {
        "box": {
          "id": "obj-oscroute",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 4,
          "outlettype": [
            "",
            "",
            "",
            ""
          ],
          "patching_rect": [
            30.0,
            110.0,
            320.0,
            22.0
          ],
          "text": "route /tape/ping /tape/record /tape/stop /tape/list"
        }
      },
      {
        "box": {
          "id": "obj-pongmsg",
          "maxclass": "message",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [
            ""
          ],
          "patching_rect": [
            30.0,
            150.0,
            100.0,
            22.0
          ],
          "text": "/tape/pong"
        }
      },
      {
        "box": {
          "id": "obj-recordtrig",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 2,
          "outlettype": [
            "",
            ""
          ],
          "patching_rect": [
            150.0,
            150.0,
            110.0,
            22.0
          ],
          "text": "unpack s 0."
        }
      },
      {
        "box": {
          "id": "obj-pathmsg",
          "maxclass": "message",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [
            ""
          ],
          "patching_rect": [
            150.0,
            185.0,
            220.0,
            22.0
          ],
          "text": "open $1, record 1"
        }
      },
      {
        "box": {
          "id": "obj-durmstomul",
          "maxclass": "newobj",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [
            ""
          ],
          "patching_rect": [
            270.0,
            185.0,
            70.0,
            22.0
          ],
          "text": "* 1000."
        }
      },
      {
        "box": {
          "id": "obj-delay",
          "maxclass": "newobj",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [
            "bang"
          ],
          "patching_rect": [
            270.0,
            215.0,
            70.0,
            22.0
          ],
          "text": "delay"
        }
      },
      {
        "box": {
          "id": "obj-stopmsg",
          "maxclass": "message",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [
            ""
          ],
          "patching_rect": [
            270.0,
            245.0,
            90.0,
            22.0
          ],
          "text": "record 0"
        }
      },
      {
        "box": {
          "id": "obj-sfrecord",
          "maxclass": "newobj",
          "numinlets": 3,
          "numoutlets": 2,
          "outlettype": [
            "",
            ""
          ],
          "patching_rect": [
            30.0,
            290.0,
            240.0,
            22.0
          ],
          "text": "sfrecord~ 2 16"
        }
      },
      {
        "box": {
          "id": "obj-plug",
          "maxclass": "newobj",
          "numinlets": 2,
          "numoutlets": 5,
          "outlettype": [
            "signal",
            "signal",
            "",
            "",
            ""
          ],
          "patching_rect": [
            30.0,
            250.0,
            60.0,
            22.0
          ],
          "text": "plugin~"
        }
      },
      {
        "box": {
          "id": "obj-plugout",
          "maxclass": "newobj",
          "numinlets": 2,
          "numoutlets": 0,
          "patching_rect": [
            30.0,
            380.0,
            70.0,
            22.0
          ],
          "text": "plugout~"
        }
      },
      {
        "box": {
          "id": "obj-donemsg",
          "maxclass": "message",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [
            ""
          ],
          "patching_rect": [
            380.0,
            245.0,
            220.0,
            22.0
          ],
          "text": "/tape/done"
        }
      },
      {
        "box": {
          "id": "obj-udpsend",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 0,
          "patching_rect": [
            380.0,
            290.0,
            200.0,
            22.0
          ],
          "text": "udpsend 127.0.0.1 11004"
        }
      },
      {
        "box": {
          "id": "obj-statustxt",
          "maxclass": "live.text",
          "mode": 0,
          "numinlets": 1,
          "numoutlets": 2,
          "outlettype": [
            "",
            ""
          ],
          "parameter_enable": 1,
          "patching_rect": [
            380.0,
            30.0,
            200.0,
            28.0
          ],
          "presentation": 1,
          "presentation_rect": [
            10.0,
            10.0,
            200.0,
            28.0
          ],
          "saved_attribute_attributes": {
            "valueof": {
              "parameter_initial": [
                "idle"
              ],
              "parameter_initial_enable": 1,
              "parameter_longname": "tape_status",
              "parameter_shortname": "status",
              "parameter_type": 3
            }
          },
          "text": "idle",
          "varname": "tape_status"
        }
      },
      {
        "box": {
          "id": "obj-portbox",
          "maxclass": "live.numbox",
          "numinlets": 1,
          "numoutlets": 2,
          "outlettype": [
            "",
            ""
          ],
          "parameter_enable": 1,
          "patching_rect": [
            380.0,
            70.0,
            80.0,
            22.0
          ],
          "presentation": 1,
          "presentation_rect": [
            220.0,
            10.0,
            80.0,
            28.0
          ],
          "saved_attribute_attributes": {
            "valueof": {
              "parameter_initial": [
                11003
              ],
              "parameter_initial_enable": 1,
              "parameter_longname": "tape_port",
              "parameter_shortname": "port",
              "parameter_type": 0
            }
          },
          "varname": "tape_port"
        }
      }
    ],
    "lines": [
      {
        "patchline": {
          "destination": [
            "obj-oscroute",
            0
          ],
          "source": [
            "obj-udprecv",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-pongmsg",
            0
          ],
          "source": [
            "obj-oscroute",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-recordtrig",
            0
          ],
          "source": [
            "obj-oscroute",
            1
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-stopmsg",
            0
          ],
          "source": [
            "obj-oscroute",
            2
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-durmstomul",
            0
          ],
          "source": [
            "obj-recordtrig",
            1
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-delay",
            0
          ],
          "source": [
            "obj-durmstomul",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-stopmsg",
            0
          ],
          "source": [
            "obj-delay",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-sfrecord",
            0
          ],
          "source": [
            "obj-pathmsg",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-sfrecord",
            0
          ],
          "source": [
            "obj-stopmsg",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-sfrecord",
            1
          ],
          "source": [
            "obj-plug",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-sfrecord",
            2
          ],
          "source": [
            "obj-plug",
            1
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-plugout",
            0
          ],
          "source": [
            "obj-plug",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-plugout",
            1
          ],
          "source": [
            "obj-plug",
            1
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-udpsend",
            0
          ],
          "source": [
            "obj-pongmsg",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-udpsend",
            0
          ],
          "source": [
            "obj-donemsg",
            0
          ]
        }
      },
      {
        "patchline": {
          "destination": [
            "obj-donemsg",
            0
          ],
          "source": [
            "obj-stopmsg",
            0
          ]
        }
      },
      {
        "patchline": {
          "source": [
            "obj-recordtrig",
            0
          ],
          "destination": [
            "obj-pathmsg",
            0
          ]
        }
      }
    ],
    "dependency_cache": [],
    "autosave": 0
  }
}