import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

ApplicationWindow {
    id: window
    width: 1180; height: 790
    minimumWidth: 980; minimumHeight: 720
    visible: true
    title: "Reframe"
    color: "#101215"
    font.family: "Segoe UI"
    font.pixelSize: 14
    property var s: controller.state
    property color muted: "#959AA6"
    property color accent: "#C3F47C"
    onClosing: function(close) { close.accepted = controller.requestClose() }

    component ActionButton: Button {
        id: button
        property bool primary: false
        implicitHeight: 44
        leftPadding: 20; rightPadding: 20
        contentItem: Text {
            text: button.text
            font.family: button.font.family
            font.pixelSize: button.font.pixelSize
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            color: !button.enabled ? "#666C75" : button.primary ? "#15220C" : "#ECEEF2"
        }
        background: Rectangle {
            radius: 10
            color: !button.enabled ? "#292D32" : button.primary ? (button.hovered ? "#D5FF9E" : window.accent) : (button.hovered ? "#343942" : "#262A31")
            border.color: button.activeFocus ? window.accent : "transparent"
        }
        Accessible.name: text
    }

    MediaPlayer {
        id: player
        source: window.s.previewUrl
        videoOutput: video
        audioOutput: AudioOutput { volume: 0.7 }
        onSourceChanged: pause()
    }
    Connections {
        target: controller
        function onChanged() { if (window.s.busy) player.pause() }
        function onCloseReady() { window.close() }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 32
        spacing: 24
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Rectangle {
                width: 36; height: 36; radius: 10; color: window.accent
                Text { anchors.centerIn: parent; text: "↗"; color: "#17250D"; font.pixelSize: 28; font.bold: true }
            }
            Text { text: "reframe"; color: "#F4F5F7"; font.pixelSize: 28; font.weight: Font.DemiBold; font.letterSpacing: -1 }
            Item { Layout.fillWidth: true }
            Rectangle { width: 7; height: 7; radius: 4; color: window.accent }
            Text { text: "Локально на вашем компьютере"; color: window.muted; font.pixelSize: 12 }
        }
        ColumnLayout {
            spacing: 6
            Text { text: "Новый кадр. Та же история."; color: "#F4F5F7"; font.pixelSize: 30; font.weight: Font.DemiBold }
            Text { text: "Выберите формат — Reframe сам удержит главное в кадре."; color: window.muted; font.pixelSize: 15 }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 24
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    Layout.minimumHeight: 260
                    radius: 16
                    color: "#090B0E"
                    border.color: drop.containsDrag ? window.accent : "#2A2E35"
                    VideoOutput { id: video; anchors.fill: parent; anchors.margins: 10; fillMode: VideoOutput.PreserveAspectFit; visible: !!window.s.previewUrl }
                    ColumnLayout {
                        anchors.centerIn: parent; spacing: 16
                        visible: !window.s.previewUrl
                        Rectangle {
                            Layout.alignment: Qt.AlignHCenter
                            width: 64; height: 64; radius: 18; color: "#21282A"
                            Text { anchors.centerIn: parent; text: "+"; color: window.accent; font.pixelSize: 34 }
                        }
                        Text { Layout.alignment: Qt.AlignHCenter; text: window.s.loading ? "Проверяем видео…" : "Перетащите видео сюда"; color: "#E9ECF0"; font.pixelSize: 21; font.weight: Font.DemiBold }
                        Text { Layout.alignment: Qt.AlignHCenter; text: "MP4, MOV, MKV, AVI · H.264 / H.265"; color: window.muted; font.pixelSize: 12 }
                        ActionButton { Layout.alignment: Qt.AlignHCenter; text: "Выбрать видео"; enabled: !window.s.loading; onClicked: controller.chooseSource() }
                    }
                    DropArea {
                        id: drop; anchors.fill: parent
                        enabled: !window.s.busy && !window.s.loading
                        onDropped: function(event) { if (event.hasUrls && event.urls.length === 1) controller.loadSource(event.urls[0].toString()) }
                    }
                    Rectangle {
                        visible: !!window.s.result
                        anchors.top: parent.top; anchors.left: parent.left; anchors.margins: 20
                        width: 148; height: 30; radius: 15; color: "#26351C"
                        Text { anchors.centerIn: parent; text: "✓  Готовый результат"; color: window.accent; font.pixelSize: 12 }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; visible: !!window.s.previewUrl
                    ActionButton {
                        text: player.playbackState === MediaPlayer.PlayingState ? "Ⅱ" : "▶"
                        enabled: !window.s.busy
                        onClicked: player.playbackState === MediaPlayer.PlayingState ? player.pause() : player.play()
                    }
                    Slider {
                        Layout.fillWidth: true; from: 0; to: Math.max(1, player.duration)
                        value: player.position; enabled: player.seekable && !window.s.busy
                        onMoved: player.setPosition(value)
                    }
                    ActionButton { text: "Другое видео"; enabled: !window.s.busy && !window.s.loading; onClicked: controller.chooseSource() }
                }
                Text { Layout.fillWidth: true; text: window.s.name; color: "#D4D8DF"; elide: Text.ElideMiddle; font.weight: Font.DemiBold }
                Text { text: window.s.metadata; color: window.muted; font.pixelSize: 12 }
                Text { Layout.fillWidth: true; visible: !!player.errorString && !!window.s.previewUrl; text: "Предпросмотр недоступен. Можно создать файл и открыть его внешним плеером."; color: "#DCC38D"; wrapMode: Text.WordWrap; font.pixelSize: 12 }
            }
            Rectangle {
                Layout.preferredWidth: 320; Layout.fillHeight: true
                color: "#1A1D22"; radius: 16; border.color: "#2B2F36"
                ScrollView {
                    id: optionsScroll
                    anchors.fill: parent; anchors.margins: 22
                    anchors.bottomMargin: 108
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: optionsScroll.availableWidth
                    height: Math.max(implicitHeight, optionsScroll.availableHeight)
                    spacing: 12
                    Text { text: "ФОРМАТ РЕЗУЛЬТАТА"; color: window.muted; font.pixelSize: 11; font.letterSpacing: 1.3; font.weight: Font.DemiBold }
                    GridLayout {
                        Layout.fillWidth: true; columns: 3; columnSpacing: 8; rowSpacing: 8
                        Repeater {
                            model: controller.formats
                            delegate: Button {
                                id: formatButton
                                required property var modelData
                                Layout.fillWidth: true; Layout.preferredHeight: 78
                                enabled: !window.s.busy
                                onClicked: controller.selectPreset(modelData.id)
                                Accessible.name: modelData.label + " " + modelData.description
                                background: Rectangle {
                                    radius: 10
                                    color: window.s.preset === formatButton.modelData.id ? "#2C3824" : formatButton.hovered ? "#2C3037" : "#22262C"
                                    border.color: window.s.preset === formatButton.modelData.id ? window.accent : "#353A43"
                                }
                                contentItem: ColumnLayout {
                                    spacing: 8
                                    Item {
                                        Layout.fillWidth: true; Layout.preferredHeight: 34
                                        Rectangle {
                                            anchors.centerIn: parent
                                            width: formatButton.modelData.ratio >= 1 ? 34 : 34 * formatButton.modelData.ratio
                                            height: formatButton.modelData.ratio >= 1 ? 34 / formatButton.modelData.ratio : 34
                                            radius: 3; color: "transparent"; border.width: 2
                                            border.color: window.s.preset === formatButton.modelData.id ? window.accent : "#A5ABB6"
                                        }
                                    }
                                    Text { Layout.alignment: Qt.AlignHCenter; text: formatButton.modelData.label; color: "#F0F2F5"; font.weight: Font.DemiBold }
                                }
                                ToolTip.visible: hovered
                                ToolTip.text: modelData.description
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "Тип файла"; color: "#D7DAE0" }
                        Item { Layout.fillWidth: true }
                        Repeater {
                            model: controller.containers
                            delegate: ActionButton {
                                required property string modelData
                                text: modelData; primary: window.s.container === modelData.toLowerCase()
                                enabled: !window.s.busy
                                leftPadding: 12; rightPadding: 12
                                onClicked: controller.selectContainer(modelData)
                            }
                        }
                    }
                    Rectangle { visible: optionsScroll.availableHeight >= 360; Layout.fillWidth: true; height: 1; color: "#30343C" }
                    Text { visible: optionsScroll.availableHeight >= 360; text: "Всё остальное — автоматически"; color: "#D7DAE0"; font.weight: Font.DemiBold }
                    Text { visible: optionsScroll.availableHeight >= 360; Layout.fillWidth: true; text: "Плавное слежение за главным объектом. Полный кадр с мягким фоном, если сцена неоднозначна."; color: window.muted; wrapMode: Text.WordWrap; font.pixelSize: 12; lineHeight: 1.35 }
                }
                }
                ColumnLayout {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    anchors.margins: 22; spacing: 12
                    ActionButton {
                        Layout.fillWidth: true; primary: true
                        text: window.s.busy ? "Обрабатываем…" : "Создать видео  ↗"
                        enabled: !!window.s.source && !window.s.busy && !window.s.loading
                        onClicked: { player.pause(); controller.start() }
                    }
                    Text { Layout.alignment: Qt.AlignHCenter; text: "Исходное видео останется без изменений"; color: window.muted; font.pixelSize: 10 }
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: statusColumn.implicitHeight + 32
            radius: 12; color: window.s.error ? "#342126" : "#1A1D22"
            ColumnLayout {
                id: statusColumn; anchors.fill: parent; anchors.margins: 16; spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: window.s.stage; color: "#EDF0F4"; font.weight: Font.DemiBold }
                    Text { visible: window.s.busy; text: window.s.elapsed; color: window.muted; font.pixelSize: 12 }
                    Item { Layout.fillWidth: true }
                    Text { visible: window.s.busy; text: Math.round(window.s.progress * 100) + "%"; color: window.accent }
                    ActionButton { visible: window.s.busy; text: "Отмена"; implicitHeight: 32; enabled: !window.s.cancelling; onClicked: controller.cancel() }
                    ActionButton { visible: !!window.s.result && !window.s.busy; text: "Открыть видео"; implicitHeight: 32; onClicked: controller.openResult() }
                    ActionButton { visible: !!window.s.result && !window.s.busy; text: "Показать в папке"; implicitHeight: 32; onClicked: controller.showFolder() }
                }
                ProgressBar {
                    Layout.fillWidth: true; visible: window.s.busy; value: window.s.progress
                    background: Rectangle { implicitHeight: 4; radius: 2; color: "#333840" }
                    contentItem: Item { implicitHeight: 4; Rectangle { width: parent.width * window.s.progress; height: parent.height; radius: 2; color: window.accent } }
                }
                Text { Layout.fillWidth: true; visible: !!(window.s.error || window.s.warning || window.s.detail); text: window.s.error || window.s.warning || window.s.detail; color: window.s.error ? "#FFB6BC" : window.muted; wrapMode: Text.WordWrap; font.pixelSize: 12 }
            }
        }
    }
}
