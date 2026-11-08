#include "fake_nvs.h"
#include "../code/main/remote_settings.h"
#include <cassert>
#include <cstdio>

static void expect(const remote_settings_t &settings, int channel, int id)
{ assert(settings.channel == channel && settings.id == id); }

int main()
{
    reset_nvs();
    remote_settings_t settings;
    assert(remote_settings_load(&settings) == ESP_ERR_NVS_NOT_FOUND);
    expect(settings, 6, 1);
    assert(write_opens == 0 && blob_writes == 0 && commits == 0);
    assert(remote_settings_confirm(&settings) == ESP_OK);
    assert(write_opens == 0); // Merely visiting/exiting setup never wears flash.

    settings = {11, 7};
    assert(remote_settings_confirm(&settings) == ESP_OK);
    assert(blob_writes == 1 && commits == 1);
    assert(stored_blob == (std::vector<uint8_t>{'R', 'C', 'F', 'G', 1, 11, 7, 0}));
    assert(remote_settings_load(&settings) == ESP_OK);
    expect(settings, 11, 7);
    const auto good = stored_blob;

    for (auto invalid : {remote_settings_t{0, 7}, {15, 7}, {-1, 7}, {256, 7}, {6, -1}, {6, 51}, {6, 256}}) {
        assert(remote_settings_confirm(&invalid) == ESP_ERR_INVALID_ARG);
        expect(invalid, 11, 7);
        assert(stored_blob == good && blob_writes == 1);
    }
    for (auto *failure : {&open_error, &write_error}) {
        *failure = ESP_FAIL;
        settings = {3, 2};
        assert(remote_settings_confirm(&settings) == ESP_FAIL);
        expect(settings, 11, 7);
        assert(stored_blob == good);
        *failure = ESP_OK;
        assert(remote_settings_load(&settings) == ESP_OK);
        expect(settings, 11, 7);
    }
    commit_error = ESP_FAIL;
    settings = {3, 2};
    assert(remote_settings_confirm(&settings) == ESP_FAIL);
    expect(settings, 3, 2); // set_blob already wrote; readback wins over old RAM.
    commit_error = ESP_OK;
    assert(remote_settings_load(&settings) == ESP_OK);
    expect(settings, 3, 2);

    write_error = ESP_ERR_NVS_REMOVE_FAILED;
    write_takes_effect_on_error = true;
    settings = {9, 4};
    assert(remote_settings_confirm(&settings) == ESP_ERR_NVS_REMOVE_FAILED);
    expect(settings, 9, 4);
    assert(remote_settings_load(&settings) == ESP_OK);
    expect(settings, 9, 4);

    // REMOVE_FAILED can expose the old value now, then the new value after
    // reinitialization. Never assert that an API error rolled flash back.
    defer_write_visibility = true;
    settings = {10, 5};
    assert(remote_settings_confirm(&settings) == ESP_ERR_NVS_REMOVE_FAILED);
    expect(settings, 9, 4);
    simulate_nvs_reinitialization();
    assert(remote_settings_load(&settings) == ESP_OK);
    expect(settings, 10, 5);
    write_error = ESP_OK;
    write_takes_effect_on_error = defer_write_visibility = false;

    fail_read_after_write = true;
    settings = {12, 6};
    assert(remote_settings_confirm(&settings) != ESP_OK);
    expect(settings, 10, 5); // Only running state can fall back if readback fails.
    fail_read_after_write = false; read_error = ESP_OK;
    assert(remote_settings_load(&settings) == ESP_OK);
    expect(settings, 12, 6);
    stored_blob = {'R','C','F','G',1,5,9,0};
    write_error = ESP_FAIL;
    settings = {8, 3};
    assert(remote_settings_confirm(&settings) == ESP_FAIL);
    expect(settings, 5, 9); // A third valid readback is still the observable pair.
    write_error = ESP_OK;
    // Broadcast remains an explicit selectable value, never a fallback.
    settings = {1, 0};
    assert(remote_settings_confirm(&settings) == ESP_OK);
    assert(remote_settings_load(&settings) == ESP_OK);
    expect(settings, 1, 0);
    settings = {14, 50};
    assert(remote_settings_confirm(&settings) == ESP_OK);
    assert(remote_settings_load(&settings) == ESP_OK);
    expect(settings, 14, 50);

    const std::vector<std::vector<uint8_t>> malformed = {
        {'R','C','F','G',1,11,7}, {'R','C','F','G',1,11,7,0,0},
        {'X','C','F','G',1,11,7,0}, {'R','C','F','G',2,11,7,0},
        {'R','C','F','G',1,0,7,0}, {'R','C','F','G',1,15,7,0},
        {'R','C','F','G',1,6,51,0}, {'R','C','F','G',1,6,1,1},
    };
    for (const auto &blob : malformed) {
        stored_blob = blob;
        const int before = blob_writes;
        assert(remote_settings_load(&settings) != ESP_OK);
        expect(settings, 6, 1);
        assert(stored_blob == blob && blob_writes == before);
    }
    read_error = ESP_FAIL;
    assert(remote_settings_load(&settings) == ESP_FAIL);
    expect(settings, 6, 1);
    puts("remote settings defaults, persistence, validation, failures: PASS");
}
